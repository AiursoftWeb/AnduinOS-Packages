//! why — a fully offline, zero-daemon LLM CLI for AnduinOS.
//!
//! ## Usage
//!
//! ```sh
//! why "Why is the sky blue?"
//! why -r "How do I use find?"
//! git diff | why -r "Generate a concise commit message"
//! why --serve              # start an OpenAI-compatible HTTP API
//! why --serve --port 8080  # custom port
//! ```
//!
//! The bundled Gemma 4 E2B model lives at
//! `/usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf`.
//! Override with the `WHY_MODEL_PATH` environment variable.

mod engine;

use std::io::{self, IsTerminal, Read};
use std::process;

use clap::Parser;

/// A fully offline, zero-daemon LLM CLI backed by a local Gemma 4 E2B model.
///
/// Ask a question in plain text, get an answer on stdout, then exit.
/// Pipe in context from stdin — logs, help pages, diffs — and let the
/// model summarise, explain, or generate.
///
/// Start `why --serve` to run an OpenAI-compatible HTTP API on localhost.
#[derive(Parser, Debug)]
#[command(name = "why", version, about, long_about = None)]
struct Cli {
    /// The question or prompt (positional). e.g. `why "Why is the sky blue?"`.
    /// When stdin is a pipe, its content is prepended as context before this prompt.
    #[arg(default_value = "")]
    prompt: String,

    /// Respond to the given text (alias for positional prompt).
    /// Both `why -r "question"` and `why "question"` are equivalent.
    #[arg(short = 'r', long = "respond")]
    respond: Option<String>,

    /// Start an OpenAI-compatible HTTP chat-completions server on localhost.
    #[arg(long, short)]
    serve: bool,

    /// Port for the HTTP server (only with --serve). Default: 8080.
    #[arg(short = 'p', long, default_value = "8080")]
    port: u16,

    /// Sampling temperature (0.0–2.0). Lower = more deterministic.
    /// Default: 0.1 (high certainty, suitable for CLI tooling).
    #[arg(short = 't', long = "temp", default_value = "0.1")]
    temperature: f32,

    /// Context window size in tokens. Default: 32768.
    #[arg(short = 'c', long, default_value = "32768")]
    context: u32,

    /// Number of tokens to generate. Default: 8192.
    #[arg(long, default_value = "8192")]
    max_tokens: i32,

    /// Number of CPU threads for token generation.
    /// Default: (CPU cores - 1), minimum 1.
    #[arg(short = 'j', long)]
    threads: Option<i32>,

    /// Number of CPU threads for prompt / batch processing.
    /// Default: (CPU cores - 1), minimum 1.
    #[arg(short = 'b', long)]
    threads_batch: Option<i32>,

    /// List all compute devices detected by llama.cpp (GPU, CPU, …).
    #[arg(long)]
    list_devices: bool,

    /// Path to the GGUF model file.
    /// Default: /usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf
    /// Env override: WHY_MODEL_PATH
    #[arg(long, env = "WHY_MODEL_PATH")]
    model: Option<String>,

    /// Disable GPU offload — force CPU-only inference.
    #[arg(long)]
    cpu_only: bool,

    /// Enable verbose llama.cpp progress output to stderr.
    #[arg(short = 'v', long)]
    verbose: bool,
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    // ── resolve thread defaults ─────────────────────────────────────────
    let cpu_count = std::thread::available_parallelism()
        .map(|n| n.get() as i32)
        .unwrap_or(4);
    let threads = cli.threads.unwrap_or((cpu_count - 1).max(1));
    let threads_batch = cli.threads_batch.unwrap_or((cpu_count - 1).max(1));

    // Resolve prompt: --respond takes precedence over positional
    let prompt_text = cli.respond.unwrap_or(cli.prompt);

    // --- list-devices mode --------------------------------------------------
    if cli.list_devices {
        return engine::list_devices();
    }

    // --- serve mode ---------------------------------------------------------
    if cli.serve {
        let model = cli.model.unwrap_or_else(|| {
            "/usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf".into()
        });

        let model_name = std::path::Path::new(&model)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("gemma-4-e2b-it-q4_k_m.gguf");

        // ── startup banner ──────────────────────────────────────────────
        let c = "\x1b[36m"; // cyan
        let r = "\x1b[0m";  // reset
        eprintln!(
            "\n\
             {c}┌──────────────────────────────────────────────────────────┐{r}\n\
             {c}│{r}  🦙 llama-server starting on http://127.0.0.1:{}     {c}│{r}\n\
             {c}│{r}  Model: {:<46} {c}│{r}\n\
             {c}│{r}  Context: {:<5} tokens  Threads: gen={} batch={}       {c}│{r}\n\
             {c}└──────────────────────────────────────────────────────────┘{r}\n\
             \n\
             {c}💡 Try it with Copilot CLI:{r}\n\
             \n\
               export COPILOT_PROVIDER_BASE_URL=\"http://127.0.0.1:{}/v1\"\n\
               export COPILOT_MODEL=\"{}\"\n\
               export COPILOT_PROVIDER_MAX_PROMPT_TOKENS={}\n\
               export COPILOT_PROVIDER_MAX_OUTPUT_TOKENS={}\n\
               copilot\n\
             ",
            cli.port,
            model_name,
            cli.context,
            threads,
            threads_batch,
            cli.port,
            model_name,
            cli.context,
            cli.max_tokens,
        );

        let mut cmd = std::process::Command::new("llama-server");
        cmd.arg("-m").arg(&model)
           .arg("--host").arg("127.0.0.1")
           .arg("--port").arg(cli.port.to_string())
           .arg("--ctx-size").arg(cli.context.to_string())
           .arg("--threads").arg(threads.to_string())
           .arg("--threads-batch").arg(threads_batch.to_string())
           .arg("--temp").arg(cli.temperature.to_string())
           .arg("--n-predict").arg(cli.max_tokens.to_string())
           .arg("--no-webui");

        if cli.verbose {
            cmd.arg("--verbose");
        }
        if cli.cpu_only {
            cmd.env("GGML_VULKAN", "0");
        }

        let status = cmd.status()?;
        std::process::exit(status.code().unwrap_or(1));
    }

    // --- chat mode ----------------------------------------------------------
    let mut stdin_context = String::new();
    let stdin_is_pipe = !io::stdin().is_terminal();
    if stdin_is_pipe {
        io::stdin()
            .read_to_string(&mut stdin_context)
            .map_err(|e| anyhow::anyhow!("Failed to read stdin: {}", e))?;
    }

    let prompt = if stdin_context.trim().is_empty() {
        prompt_text.clone()
    } else if prompt_text.is_empty() {
        stdin_context.clone()
    } else {
        format!(
            "Context:\n{}\n\nQuestion: {}",
            stdin_context.trim(),
            prompt_text
        )
    };

    if prompt.trim().is_empty() {
        eprintln!("usage: why <PROMPT>");
        eprintln!("       why -r <PROMPT>");
        eprintln!("       <stdin> | why [-r <question>]");
        eprintln!("       why --serve");
        eprintln!("Try 'why --help' for more information.");
        process::exit(1);
    }

    let model_path = cli.model.unwrap_or_else(|| {
        "/usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf".into()
    });

    if cli.verbose {
        eprintln!("[why] model: {}", model_path);
        eprintln!(
            "[why] prompt ({} chars): {}",
            prompt.len(),
            &prompt[..prompt.len().min(200)]
        );
    }

    engine::chat(
        &model_path,
        &prompt,
        cli.context,
        cli.max_tokens,
        threads,
        threads_batch,
        cli.temperature,
        cli.cpu_only,
        cli.verbose,
    )?;

    Ok(())
}
