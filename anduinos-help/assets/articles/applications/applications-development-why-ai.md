# Why AI

!!! tip "AnduinOS Verified App - Open Source"

    Why AI is an AnduinOS verified app and it runs awesome on AnduinOS, with easy installation and automatic updates.

`anduinos-why-ai` is a **zero-daemon, 100% offline CLI AI tool** built into AnduinOS 2.0+. Powered by `llama.cpp` and shipping with a pre-tuned Google Gemma 4 E2B (Q4_K_M) model, it delivers private, on-device AI inference with no internet connection, no background services, and zero privacy risk.

## Install Why AI

Why AI is available in the AnduinOS repository. Install it with:

```bash
sudo apt install anduinos-why-ai
```

## Basic Usage

`why` supports **direct questions**, **pipe-based context input**, and an **offline API server mode**.

```bash
# Ask a question directly
why "Why is the sky blue?"

# Use -r / --respond (equivalent to positional prompt)
why -r "Explain quantum mechanics in three short sentences"

# Pipe context into the prompt
cat error.log | why -r "Analyze this log and identify the root cause"
```

## Examples

### 1. System Configuration & Hardware Audit

Pipe `fastfetch` or system diagnostics output directly into `why` for an instant read on your system's configuration and potential bottlenecks.

```bash
fastfetch | why -r "Analyze this system configuration — any performance bottlenecks or notable hardware characteristics?"
```

### 2. Generate Git Commit Messages

Before committing, pipe your staged diff into `why` to generate a Conventional Commits style message automatically.

```bash
git diff --cached | why -r "Generate a short commit message following the Conventional Commits spec. Output only the message."
```

### 3. Log Analysis & Troubleshooting

When a service fails to start or crashes, pipe the logs directly instead of copying errors into a search engine.

```bash
journalctl -u nginx -n 50 --no-pager | why -r "Why did my Nginx service fail to start? Give me step-by-step troubleshooting instructions."
```

### 4. Shell One-Liner Generator

Forgotten a complex command? Ask `why` in natural language and get the exact one-liner back.

```bash
why -r "Write a Linux command to find all .log files larger than 100 MB in the current directory and sort them by size. Output only the command."
```

### 5. Start a Local OpenAI-Compatible API Server

Run `why -s` to launch a local HTTP server compatible with the OpenAI Chat Completions API (default port `8080`), with Vulkan / CUDA hardware acceleration. Use it as a drop-in backend for GitHub Copilot CLI or any local AI plugin.

```bash
# Start the local server
why -s

# In another terminal, configure and launch Copilot CLI
export COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:8080/v1"
export COPILOT_MODEL="gemma-4-e2b-it-q4_k_m.gguf"
export COPILOT_PROVIDER_MAX_PROMPT_TOKENS=32768
export COPILOT_PROVIDER_MAX_OUTPUT_TOKENS=8192
copilot
```

## Advanced Options

| Parameter | Description | Default |
|---|---|---|
| `-s, --serve` | Start an OpenAI-compatible HTTP chat-completions server | Disabled |
| `-p, --port` | HTTP server port (only with `--serve`) | `8080` |
| `-t, --temp` | Sampling temperature (0.0–2.0). Lower = more deterministic | `0.1` |
| `-c, --context` | Context window size in tokens | `32768` |
| `--max-tokens` | Number of tokens to generate | `8192` |
| `-j, --threads` | Number of CPU threads for generation | CPU cores − 1 |
| `-b, --threads-batch` | Number of CPU threads for prompt / batch processing | CPU cores − 1 |
| `--cpu-only` | Disable GPU offload — force CPU-only inference | Disabled |
| `--model` | Path to a custom GGUF model file | Bundled Gemma 4 E2B |
| `--list-devices` | List all compute devices detected by llama.cpp | — |
| `-v, --verbose` | Enable verbose llama.cpp progress output to stderr | Disabled |
