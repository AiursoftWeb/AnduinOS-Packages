//! Inference engine — loads the bundled GGUF model and streams tokens to stdout.
//!
//! Key design decisions:
//! - llama.cpp stderr chatter is **fully suppressed** unless `verbose` is set.
//!   This keeps stdout pure for Unix pipelines (`why | grep ...`).
//! - Prompts are formatted in the native Gemma 4 control-token schema
//!   (`<|turn>user`, `<turn|>`, `<|turn>model`).  No Jinja template engine,
//!   no system prompt — Gemma only supports `user` / `model` roles.
//! - When input exceeds the context window, the **head** of the input is
//!   truncated (keeping the tail), not a hard error.  Logs and diffs have
//!   their key info at the end; the user never sees a "Prompt too long"
//!   bail-out.
//! - GPU offload is attempted first; if model loading fails (missing / broken
//!   Vulkan driver), the engine automatically retries with CPU-only.

use std::io::{self, Write};
use std::num::NonZeroU32;
use std::path::Path;
use std::pin::pin;

use anyhow::{bail, Context};
use llama_cpp_2::context::params::LlamaContextParams;
use llama_cpp_2::llama_backend::LlamaBackend;
use llama_cpp_2::llama_batch::LlamaBatch;
use llama_cpp_2::model::params::LlamaModelParams;
use llama_cpp_2::model::{AddBos, LlamaModel};
use llama_cpp_2::sampling::LlamaSampler;
use llama_cpp_2::{send_logs_to_tracing, LogOptions};

const DEFAULT_N_CTX: u32 = 8192;

// ── device listing ───────────────────────────────────────────────────────────

/// Print all compute devices detected by llama.cpp.
pub fn list_devices() -> anyhow::Result<()> {
    let _backend = LlamaBackend::init()?;
    let devices = llama_cpp_2::list_llama_ggml_backend_devices();
    if devices.is_empty() {
        println!("No compute devices detected.");
        return Ok(());
    }
    for (i, dev) in devices.iter().enumerate() {
        println!("Device {i:>2}: {}", dev.name);
        println!("           Description: {}", dev.description);
        println!("           Device Type: {:?}", dev.device_type);
        println!("           Backend: {}", dev.backend);
        println!(
            "           Memory total: {:?} MiB",
            dev.memory_total / 1024 / 1024
        );
        println!(
            "           Memory free:  {:?} MiB",
            dev.memory_free / 1024 / 1024
        );
    }
    Ok(())
}

// ── chat entry point ─────────────────────────────────────────────────────────

/// Run a single-turn chat completion, streaming tokens to stdout.
///
/// The model is loaded from `model_path` and prompted with the native Gemma 4
/// control-token format.  Generated tokens are printed to stdout as they
/// arrive (with special-token suppression).  llama.cpp internal logs are
/// suppressed unless `verbose` is true.
///
/// If GPU offload fails (missing / broken driver), the function automatically
/// retries with CPU-only.
pub fn chat(
    model_path: &str,
    prompt: &str,
    max_tokens: i32,
    threads: Option<i32>,
    temperature: f32,
    cpu_only: bool,
    verbose: bool,
) -> anyhow::Result<()> {
    // Silence llama.cpp stderr chatter by default (pure stdout for pipes).
    send_logs_to_tracing(LogOptions::default().with_logs_enabled(verbose));

    // --- init backend -------------------------------------------------------
    let backend = LlamaBackend::init()?;

    // --- load model (with GPU → CPU fallback) ------------------------------
    let model_path = Path::new(model_path);
    if !model_path.exists() {
        bail!(
            "Model file not found: {}\n\
             Install the anduinos-why-ai package or set WHY_MODEL_PATH.",
            model_path.display()
        );
    }

    let model = if cpu_only {
        let params = LlamaModelParams::default().with_n_gpu_layers(0);
        let params = pin!(params);
        LlamaModel::load_from_file(&backend, model_path, &params)
            .with_context(|| format!("Failed to load model from {}", model_path.display()))?
    } else {
        // Try GPU first; fall back to CPU if the driver is missing or broken.
        match try_load(&backend, model_path, 1000) {
            Ok(m) => m,
            Err(gpu_err) => {
                eprintln!(
                    "[why] GPU offload failed: {}. Retrying with CPU-only.",
                    gpu_err
                );
                let params = LlamaModelParams::default().with_n_gpu_layers(0);
                let params = pin!(params);
                LlamaModel::load_from_file(&backend, model_path, &params)
                    .with_context(|| format!("Failed to load model from {}", model_path.display()))?
            }
        }
    };

    // --- build Gemma 4 prompt (exact format from official docs) -------------
    // Gemma 4 IT models use pipe-style control tokens:
    //   <|turn>user\n{message}<turn|>\n<|turn>model\n
    //
    // The model may emit a <|channel>thought … <channel|> reasoning block
    // before the actual response; we strip that block in the output filter.
    let formatted_prompt = format!(
        "<|turn>user\n{}<turn|>\n<|turn>model\n",
        prompt
    );

    if verbose {
        eprintln!(
            "[why] formatted prompt ({} chars):\n{}",
            formatted_prompt.len(),
            &formatted_prompt[..formatted_prompt.len().min(500)]
        );
    }

    // --- tokenize -----------------------------------------------------------
    let mut tokens_list = model
        .str_to_token(&formatted_prompt, AddBos::Always)
        .with_context(|| "Failed to tokenize prompt")?;

    let n_ctx = DEFAULT_N_CTX as i32;

    // -- truncation instead of bail-out (logs/diffs have key info at tail) --
    let max_input_tokens = n_ctx as usize - max_tokens as usize - 10;
    if tokens_list.len() > max_input_tokens {
        if verbose {
            eprintln!(
                "[why] Truncating input: {} tokens → {} tokens (context limit)",
                tokens_list.len(),
                max_input_tokens
            );
        }
        // Keep the tail; head usually contains less useful context.
        tokens_list = tokens_list.split_off(tokens_list.len() - max_input_tokens);
    }

    // --- create context -----------------------------------------------------
    // n_batch / n_ubatch must be >= the largest single decode we'll ever issue.
    let batch_size = (tokens_list.len().max(512) as u32).min(n_ctx as u32);
    let mut ctx_params = LlamaContextParams::default()
        .with_n_ctx(Some(NonZeroU32::new(n_ctx as u32).unwrap()))
        .with_n_batch(batch_size)
        .with_n_ubatch(batch_size);
    if let Some(t) = threads {
        ctx_params = ctx_params.with_n_threads(t).with_n_threads_batch(t);
    }

    let mut ctx = model
        .new_context(&backend, ctx_params)
        .with_context(|| "Failed to create llama context")?;

    // --- initial decode -----------------------------------------------------
    let mut batch = LlamaBatch::new(batch_size as usize, 1);
    let last_idx = (tokens_list.len() - 1) as i32;
    for (i, token) in (0_i32..).zip(tokens_list.into_iter()) {
        let is_last = i == last_idx;
        batch.add(token, i, &[0], is_last)?;
    }
    ctx.decode(&mut batch)
        .with_context(|| "Initial decode failed")?;

    // --- generation loop ----------------------------------------------------
    let mut n_cur = batch.n_tokens();

    let mut sampler = if temperature <= 0.0 {
        LlamaSampler::chain_simple([LlamaSampler::dist(1234), LlamaSampler::greedy()])
    } else {
        // NOTE: top_k/top_p/min_p trigger GGML_ASSERT failures in this
        // llama.cpp version across multiple architectures.
        // Use temperature-only until llama.cpp is updated.
        LlamaSampler::chain_simple([
            LlamaSampler::dist(1234),
            LlamaSampler::temp(temperature),
        ])
    };

    // Stream output token-by-token, with inline special-token suppression.
    let mut decoder = encoding_rs::UTF_8.new_decoder();
    let mut stdout = io::stdout();
    let mut buf: Vec<u8> = Vec::with_capacity(128); // look-back for tag detection

    while n_cur <= max_tokens {
        let token = sampler.sample(&ctx, batch.n_tokens() - 1);
        sampler.accept(token);

        if model.is_eog_token(token) {
            break;
        }

        let piece = token_to_str_retry(&model, token, &mut decoder)?;
        buf.extend_from_slice(piece.as_bytes());

        // Flush safe bytes (not inside a <think> block, not a special tag).
        let safe_len = safe_prefix_len(&buf);
        if safe_len > 0 {
            let safe = String::from_utf8_lossy(&buf[..safe_len]);
            print!("{}", safe);
            stdout.flush()?;
            buf.drain(..safe_len);
        }

        batch.clear();
        batch.add(token, n_cur, &[0], true)?;

        n_cur += 1;
        ctx.decode(&mut batch)
            .with_context(|| "Decode step failed")?;
    }

    // Flush any remaining buffered bytes (strip trailing tags).
    let remainder = strip_special_tokens(&buf);
    let s = String::from_utf8_lossy(&remainder);
    if !s.trim().is_empty() {
        print!("{}", s.trim());
    }
    println!(); // trailing newline
    Ok(())
}

/// Known suppressible control tokens.  Must be kept in sync with
/// `strip_special_tokens`.
const SUPPRESSIBLE_TAGS: &[&[u8]] = &[
    // Legacy Gemma 2 / Qwen
    b"<think>",
    b"</think>",
    b"<end_of_turn>",
    b"</end_of_turn>",
    b"<start_of_turn>",
    b"</start_of_turn>",
    // Gemma 4
    b"<|channel>thought",
    b"<channel|>",
    b"<turn|>",
    b"<|turn>",
    b"<|think|>",
];

/// Return the length of the prefix of `buf` that is "safe" to emit
/// immediately.  Bytes past this point may be part of a tag that we
/// need more look-ahead to identify.
fn safe_prefix_len(buf: &[u8]) -> usize {
    if buf.is_empty() {
        return 0;
    }
    let mut i = 0;
    while i < buf.len() {
        if buf[i] == b'<' {
            let rest = &buf[i..];

            // --- <think>...</think> reasoning blocks (legacy / Qwen) ----------
            if rest.starts_with(b"<think>") {
                if let Some(end) = find_subslice(rest, b"</think>") {
                    i += end + 8; // skip past </think>
                    continue;
                }
                return i; // incomplete block — wait
            }

            // --- <|channel>thought … <channel|> reasoning blocks (Gemma 4) ----
            if rest.starts_with(b"<|channel>thought") {
                if let Some(end) = find_subslice(rest, b"<channel|>") {
                    i += end + b"<channel|>".len();
                    continue;
                }
                return i; // incomplete block — wait
            }

            // --- any other known suppressible tag (or a partial match) --------
            // If the bytes starting at '<' could be the start of a tag we
            // recognise, we MUST NOT emit them yet — a future token may
            // complete the tag.  Return the position just before the '<' so
            // the tag stays in the buffer and is eventually stripped by
            // `strip_special_tokens`.
            if is_tag_prefix(rest) {
                return i;
            }

            // Not a known tag — emit the '<' and continue
            i += 1;
            continue;
        }
        i += 1;
    }
    i
}

/// Check whether `s` *is* a known suppressible tag, is a prefix of one
/// (i.e. the bytes seen so far could turn into a tag once more data arrives),
/// or starts with one (complete tag followed by additional content such as
/// `<start_of_turn>model\n`).
fn is_tag_prefix(s: &[u8]) -> bool {
    SUPPRESSIBLE_TAGS.iter().any(|tag| {
        let cmp_len = s.len().min(tag.len());
        s[..cmp_len] == tag[..cmp_len]
    })
}

/// Find the byte position of `needle` in `haystack`.
fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// Strip model-specific control tokens from output.
///
/// Handles legacy Gemma 2 (`<start_of_turn>`, `<end_of_turn>`), Qwen
/// (`<think>…</think>`), and Gemma 4 (`<|turn>`, `<turn|>`,
/// `<|channel>thought…<channel|>`).
pub fn strip_special_tokens(bytes: &[u8]) -> Vec<u8> {
    let mut result = Vec::with_capacity(bytes.len());
    let mut depth: u32 = 0; // suppress-depth for paired blocks
    let mut i = 0;
    while i < bytes.len() {
        // --- <think>…</think> blocks (legacy / Qwen) --------------------------
        if bytes[i..].starts_with(b"<think>") {
            depth += 1;
            i += b"<think>".len();
            continue;
        }
        if bytes[i..].starts_with(b"</think>") {
            if depth > 0 { depth -= 1; }
            i += b"</think>".len();
            continue;
        }

        // --- <|channel>thought … <channel|> blocks (Gemma 4) ------------------
        if bytes[i..].starts_with(b"<|channel>thought") {
            depth += 1;
            i += b"<|channel>thought".len();
            continue;
        }
        if bytes[i..].starts_with(b"<channel|>") {
            if depth > 0 { depth -= 1; }
            i += b"<channel|>".len();
            continue;
        }

        // --- Gemma 4 turn markers ---------------------------------------------
        if bytes[i..].starts_with(b"<|turn>") {
            // skip entire line (turn-start tags like <|turn>user, <|turn>model)
            while i < bytes.len() && bytes[i] != b'\n' { i += 1; }
            if i < bytes.len() { i += 1; }
            continue;
        }
        if bytes[i..].starts_with(b"<turn|>") {
            i += b"<turn|>".len();
            continue;
        }
        if bytes[i..].starts_with(b"<|think|>") {
            i += b"<|think|>".len();
            continue;
        }

        // --- Legacy Gemma 2 turn markers --------------------------------------
        if bytes[i..].starts_with(b"<end_of_turn>") {
            i += b"<end_of_turn>".len();
            continue;
        }
        if bytes[i..].starts_with(b"</end_of_turn>") {
            i += b"</end_of_turn>".len();
            continue;
        }
        if bytes[i..].starts_with(b"<start_of_turn>") {
            while i < bytes.len() && bytes[i] != b'\n' { i += 1; }
            if i < bytes.len() { i += 1; }
            continue;
        }
        if bytes[i..].starts_with(b"</start_of_turn>") {
            i += b"</start_of_turn>".len();
            continue;
        }

        // --- Generic: standalone <...> tag at line start ----------------------
        if bytes[i] == b'<' && (i == 0 || bytes[i.saturating_sub(1)] == b'\n') {
            let start = i;
            while i < bytes.len() && bytes[i] != b'>' { i += 1; }
            if i < bytes.len() {
                i += 1;
                if i < bytes.len() && bytes[i] == b'\n' { i += 1; }
                if i - start <= 30 { continue; }
                result.extend_from_slice(&bytes[start..i]);
                continue;
            }
            break; // truncated tag at end — drop
        }

        if depth == 0 {
            result.push(bytes[i]);
        }
        i += 1;
    }
    result
}

// ── helpers ──────────────────────────────────────────────────────────────────

/// Like `model.token_to_piece()`, but retries with exponentially larger
/// buffers.  llama-cpp-2's built-in retry (8 → 512) is insufficient for
/// tokens that contain very long byte sequences (e.g. whitespace runs or
/// base64 blobs in some GGUF vocabularies).
pub fn token_to_str_retry(
    model: &LlamaModel,
    token: llama_cpp_2::token::LlamaToken,
    decoder: &mut encoding_rs::Decoder,
) -> anyhow::Result<String> {
    let mut cap: usize = 8;
    loop {
        match model.token_to_piece_bytes(token, cap, true, None) {
            Ok(bytes) => {
                let mut out = String::with_capacity(bytes.len());
                let _ = decoder.decode_to_string(&bytes, &mut out, false);
                return Ok(out);
            }
            Err(llama_cpp_2::TokenToStringError::InsufficientBufferSpace(n)) => {
                let needed = (-n) as usize;
                cap = cap.max(needed).saturating_mul(2);
                if cap > 65536 {
                    anyhow::bail!("Token too large (>{})", needed);
                }
            }
            Err(e) => anyhow::bail!("{}", e),
        }
    }
}

/// Try loading the model with `n_gpu_layers` offloaded. Returns the model on
/// success, or the original error on failure.
fn try_load(
    backend: &LlamaBackend,
    path: &Path,
    n_gpu_layers: u32,
) -> Result<LlamaModel, anyhow::Error> {
    let params = LlamaModelParams::default().with_n_gpu_layers(n_gpu_layers);
    let params = pin!(params);
    LlamaModel::load_from_file(backend, path, &params)
        .with_context(|| format!("GPU load failed for {}", path.display()))
        .map_err(|e| e.into())
}
