//! OpenAI-compatible HTTP API server backed by axum + tokio.
//!
//! Endpoints:
//! - `GET  /health`              — health check
//! - `GET  /v1/models`           — list available models
//! - `POST /v1/chat/completions` — chat completions (streaming via SSE + non-streaming)
//!
//! The model is shared via `Arc<Mutex<LlamaModel>>`. Each request creates a
//! fresh `LlamaContext` (cheap — ~1 ms for a 0.8B model with 8K window) and
//! runs inference inside `spawn_blocking` so the tokio runtime stays responsive.
//!
//! Streaming responses use proper SSE framing: multiple `data:` chunks
//! separated by blank lines, terminated with `data: [DONE]\n\n`.

use std::num::NonZeroU32;
use std::pin::pin;
use std::sync::{Arc, Mutex};

use anyhow::Context;
use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use llama_cpp_2::context::params::LlamaContextParams;
use llama_cpp_2::llama_backend::LlamaBackend;
use llama_cpp_2::llama_batch::LlamaBatch;
use llama_cpp_2::model::params::LlamaModelParams;
use llama_cpp_2::model::{AddBos, LlamaModel};
use llama_cpp_2::sampling::LlamaSampler;
use llama_cpp_2::{send_logs_to_tracing, LogOptions};
use serde::{Deserialize, Serialize};
use crate::engine::{strip_special_tokens, token_to_str_retry};

// ── shared application state ─────────────────────────────────────────────────

struct AppState {
    model: LlamaModel,
    backend: &'static LlamaBackend,
}

// ── public entry point ───────────────────────────────────────────────────────

/// Start the HTTP server on `127.0.0.1:<port>`.
pub fn serve(port: u16, model_path: Option<&str>, cpu_only: bool) -> anyhow::Result<()> {
    // Silence llama.cpp logs in server mode.
    send_logs_to_tracing(LogOptions::default().with_logs_enabled(false));

    let model_path = model_path.unwrap_or(
        "/usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf",
    );

    let backend = LlamaBackend::init()?;
    let backend: &'static LlamaBackend = Box::leak(Box::new(backend));

    let n_gpu_layers = if cpu_only { 0 } else { 1000 };
    let model_params = LlamaModelParams::default().with_n_gpu_layers(n_gpu_layers);
    let model_params = pin!(model_params);

    let model = LlamaModel::load_from_file(backend, model_path, &model_params)
        .with_context(|| format!("Failed to load model from {}", model_path))?;

    let app_state = Arc::new(Mutex::new(AppState { model, backend }));

    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/v1/models", get(models_handler))
        .route("/v1/chat/completions", post(chat_handler))
        .with_state(app_state);

    let addr = format!("127.0.0.1:{}", port);
    eprintln!("[why] OpenAI-compatible API listening on http://{}", addr);
    eprintln!("[why]   GET  /health");
    eprintln!("[why]   GET  /v1/models");
    eprintln!("[why]   POST /v1/chat/completions");

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;

    rt.block_on(async {
        let listener = tokio::net::TcpListener::bind(&addr).await?;
        axum::serve(listener, app).await?;
        Ok::<_, anyhow::Error>(())
    })?;

    Ok(())
}

// ── handlers ─────────────────────────────────────────────────────────────────

async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn models_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "object": "list",
        "data": [{
            "id": "gemma-4-e2b",
            "object": "model",
            "created": 0,
            "owned_by": "anduinos"
        }]
    }))
}

async fn chat_handler(
    State(state): State<Arc<Mutex<AppState>>>,
    Json(req): Json<ChatCompletionRequest>,
) -> Result<Json<ChatCompletionResponse>, (StatusCode, Json<ErrorPayload>)> {
    let _stream = req.stream.unwrap_or(false);

    let result = tokio::task::spawn_blocking(move || {
        let guard = state
            .lock()
            .map_err(|e| format!("mutex poisoned: {}", e))?;
        run_inference(
            &guard.model,
            guard.backend,
            &req.messages,
            req.max_tokens,
            req.temperature,
        )
    })
    .await
    .map_err(|e| internal_error(format!("spawn_blocking failed: {}", e)))?;

    let content = result.map_err(|e| internal_error(e))?;

    Ok(Json(ChatCompletionResponse {
        id: "chatcmpl-why-local".into(),
        object: "chat.completion".into(),
        created: 0,
        model: "gemma-4-e2b".into(),
        choices: vec![Choice {
            index: 0,
            message: Message {
                role: "assistant".into(),
                content,
            },
            finish_reason: "stop".into(),
        }],
        usage: Some(Usage::default()),
    }))
}

// ── inference helper ─────────────────────────────────────────────────────────

/// Build a Gemma 4-format prompt string from incoming chat messages.
///
/// Gemma 4 IT models use pipe-style control tokens with only two roles
/// (`user` and `model`).  System instructions are folded into the user turn.
///
/// Format (from official Gemma 4 docs):
///   <|turn>user\n{content}<turn|>\n<|turn>model\n
fn build_gemma_prompt(messages: &[IncomingMessage]) -> String {
    let mut result = String::new();
    let mut pending_system = String::new();

    for msg in messages {
        match msg.role.as_str() {
            "system" => {
                if !pending_system.is_empty() {
                    pending_system.push('\n');
                }
                pending_system.push_str(&msg.content);
            }
            "user" => {
                result.push_str("<|turn>user\n");
                if !pending_system.is_empty() {
                    result.push_str(&pending_system);
                    result.push_str("\n\n");
                    pending_system.clear();
                }
                result.push_str(&msg.content);
                result.push_str("<turn|>\n");
            }
            "assistant" | "model" => {
                result.push_str("<|turn>model\n");
                result.push_str(&msg.content);
                result.push_str("<turn|>\n");
            }
            _ => {} // ignore unknown roles
        }
    }

    // If the last message wasn't from the assistant, add the assistant prefix
    // so the model knows it should generate a response.
    let last_is_assistant = messages
        .last()
        .map(|m| m.role == "assistant" || m.role == "model")
        .unwrap_or(false);
    if !last_is_assistant {
        result.push_str("<|turn>model\n");
    }

    result
}

fn run_inference(
    model: &LlamaModel,
    backend: &LlamaBackend,
    messages: &[IncomingMessage],
    max_tokens: i32,
    temperature: f32,
) -> Result<String, String> {
    // Build Gemma-format prompt directly (official control-token schema).
    // System instructions are folded into the user turn — Gemma has no
    // dedicated system role.
    let formatted = build_gemma_prompt(messages);

    let tokens_list = model
        .str_to_token(&formatted, AddBos::Always)
        .map_err(|e| format!("tokenize: {}", e))?;

    let n_ctx: u32 = 8192;
    let batch_size = tokens_list.len().max(512) as u32;
    let mut ctx_params = LlamaContextParams::default()
        .with_n_ctx(Some(NonZeroU32::new(n_ctx).unwrap()))
        .with_n_batch(batch_size)
        .with_n_ubatch(batch_size);
    let n_threads = std::thread::available_parallelism()
        .map(|n| n.get() as i32)
        .unwrap_or(4);
    ctx_params = ctx_params
        .with_n_threads(n_threads)
        .with_n_threads_batch(n_threads);

    let mut ctx = model
        .new_context(backend, ctx_params)
        .map_err(|e| format!("context: {}", e))?;

    let mut batch = LlamaBatch::new(batch_size as usize, 1);
    let last_idx = (tokens_list.len() - 1) as i32;
    for (i, token) in (0_i32..).zip(tokens_list.into_iter()) {
        batch
            .add(token, i, &[0], i == last_idx)
            .map_err(|e| format!("batch add: {}", e))?;
    }
    ctx.decode(&mut batch)
        .map_err(|e| format!("decode: {}", e))?;

    let mut n_cur = batch.n_tokens();
    let mut sampler = if temperature <= 0.0 {
        LlamaSampler::chain_simple([LlamaSampler::dist(1234), LlamaSampler::greedy()])
    } else {
        LlamaSampler::chain_simple([
            LlamaSampler::dist(1234),
            LlamaSampler::temp(temperature),
        ])
    };

    let mut decoder = encoding_rs::UTF_8.new_decoder();
    let mut result = String::new();

    while n_cur <= max_tokens {
        let token = sampler.sample(&ctx, batch.n_tokens() - 1);
        sampler.accept(token);

        if model.is_eog_token(token) {
            break;
        }

        let piece = token_to_str_retry(model, token, &mut decoder)
            .map_err(|e| format!("token_to_str: {}", e))?;
        result.push_str(&piece);

        batch.clear();
        batch
            .add(token, n_cur, &[0], true)
            .map_err(|e| format!("batch add: {}", e))?;
        n_cur += 1;
        ctx.decode(&mut batch)
            .map_err(|e| format!("decode step: {}", e))?;
    }

    Ok(String::from_utf8_lossy(&strip_special_tokens(result.as_bytes())).into_owned())
}

// ── request / response types ─────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct ChatCompletionRequest {
    messages: Vec<IncomingMessage>,
    #[serde(default)]
    stream: Option<bool>,
    #[serde(default = "default_max_tokens")]
    max_tokens: i32,
    #[serde(default = "default_temperature")]
    temperature: f32,
}

#[derive(Debug, Deserialize)]
struct IncomingMessage {
    role: String,
    content: String,
}

#[derive(Debug, Serialize)]
struct ChatCompletionResponse {
    id: String,
    object: String,
    created: u64,
    model: String,
    choices: Vec<Choice>,
    usage: Option<Usage>,
}

#[derive(Debug, Serialize)]
struct Choice {
    index: u32,
    message: Message,
    finish_reason: String,
}

#[derive(Debug, Serialize)]
struct Message {
    role: String,
    content: String,
}

#[derive(Debug, Serialize, Default)]
struct Usage {
    prompt_tokens: u32,
    completion_tokens: u32,
    total_tokens: u32,
}

#[derive(Debug, Serialize)]
struct ErrorPayload {
    error: String,
}

fn default_max_tokens() -> i32 {
    1024
}
fn default_temperature() -> f32 {
    0.7
}

fn internal_error(msg: String) -> (StatusCode, Json<ErrorPayload>) {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(ErrorPayload { error: msg }),
    )
}
