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
mod server;

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

    /// Number of tokens to generate. Default: 1024.
    #[arg(long, default_value = "1024")]
    max_tokens: i32,

    /// Number of CPU threads for inference. Default: auto-detect.
    #[arg(short = 'j', long)]
    threads: Option<i32>,

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

    // Resolve prompt: --respond takes precedence over positional
    let prompt_text = cli.respond.unwrap_or(cli.prompt);

    // --- list-devices mode --------------------------------------------------
    if cli.list_devices {
        return engine::list_devices();
    }

    // --- serve mode ---------------------------------------------------------
    if cli.serve {
        return server::serve(cli.port, cli.model.as_deref(), cli.cpu_only);
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
        cli.max_tokens,
        cli.threads,
        cli.temperature,
        cli.cpu_only,
        cli.verbose,
    )?;

    Ok(())
}
