# anduinos-why-ai (`why`)

A fully offline, zero-daemon LLM CLI for AnduinOS, backed by a bundled
[Gemma 4 E2B](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF) model
over llama.cpp with Vulkan GPU acceleration.

This package targets AnduinOS 2.x (`resolute-addon`) only. Serve mode uses
the Resolute `llama.cpp-tools` package, which provides `llama-server`.

## Usage

```sh
why "Why is the sky blue?"
why -r "How do I use find?"
git diff | why -r "Generate a concise commit message"
why --serve              # start an OpenAI-compatible HTTP API on :8080
why --serve -c 32768     # custom context size
```

Two modes:

| Mode | Flag | Description |
|---|---|---|
| **Chat** | (default) | Single-turn prompt → stream tokens → exit. Pipes accepted. |
| **Serve** | `--serve` | Spawns `llama-server` on `127.0.0.1:<port>` with an OpenAI-compatible `/v1/chat/completions` endpoint. |

## Key parameters

| Flag | Default | Description |
|---|---|---|
| `-c, --context` | 32768 | Context window (tokens) |
| `--max-tokens` | 8192 | Max tokens to generate |
| `-t, --temp` | 0.1 | Sampling temperature |
| `-j, --threads` | CPU cores − 1 | Generation threads |
| `-b, --threads-batch` | CPU cores − 1 | Prompt / batch processing threads |
| `--model` | (bundled GGUF) | Override model path, or set `WHY_MODEL_PATH` |
| `--cpu-only` | off | Disable GPU offload |
| `-v, --verbose` | off | Verbose llama.cpp logs to stderr |

## Architecture

```
src/
├── main.rs    CLI (clap), prompt assembly, serve-mode bootstrap
└── engine.rs  llama.cpp inference via llama-cpp-2 crate
                Gemma 4 control-token formatting
                Streaming output with inline tag suppression
```

The bundled model lives at:
```
/usr/share/anduinos-why-ai/models/gemma-4-e2b-it-q4_k_m.gguf
```

## Model reproducibility and license

The bundled quantized model is downloaded from
[`unsloth/gemma-4-E2B-it-GGUF`](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF)
at the immutable revision
`0314792d7f1f7e229411f620751375812bb9faf2`. Its expected SHA-256 is:

```text
740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8
```

The model card attributes the base model to Google DeepMind, the GGUF
quantization to Unsloth, and declares the model under Apache License 2.0.
The packaged software remains GPL-3.0-or-later. Model attribution and the
full model license are shipped as `MODEL-NOTICE` and `MODEL-LICENSE`.

## Build dependencies

### Native (amd64)

| Package | Purpose | Stage |
|---|---|---|
| `rustc` `cargo` | Rust compiler & package manager | compile |
| `build-essential` `gcc` `pkg-config` | C compiler & library detection | compile |
| `cmake` | llama.cpp C++ build system | compile |
| `libvulkan-dev` | Vulkan headers & loader | compile + link |
| `libclang-dev` `libclang-21-dev` | libclang.so for bindgen FFI generation | compile |
| `glslc` | Vulkan GLSL shader compiler | compile |
| `spirv-headers` | SPIR-V cmake config (Vulkan shader toolchain) | compile |

### Cross-compilation (arm64)

| Package | Purpose | Stage |
|---|---|---|
| `gcc-aarch64-linux-gnu` `g++-aarch64-linux-gnu` | C/C++ cross-compiler | compile |
| `libvulkan-dev:arm64` | Vulkan arm64 libraries | link |
| `libclang-dev:arm64` `libclang1-21:arm64` | clang arm64 libraries | link |
| `libstd-rust-dev:arm64` | Rust arm64 standard library | link |

> **Note:** `libgtk-4-dev`, `libglib2.0-dev`, `libadwaita-1-dev` (and their
> `:arm64` variants) are also installed in the CI runner image but are used by
> other projects (`anduinos-deskmon`, `ufwall-gtk`), not by `why` itself.

### One-liner for a fresh machine

```sh
# native
sudo apt install -y rustc cargo build-essential gcc pkg-config \
  cmake libvulkan-dev libclang-dev libclang-21-dev glslc spirv-headers

# arm64 cross (add architecture first)
sudo dpkg --add-architecture arm64 && sudo apt update
sudo apt install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
  libvulkan-dev:arm64 libclang-dev:arm64 libclang1-21:arm64 \
  libstd-rust-dev:arm64
```

## Building

```sh
# Single arch, single suite (fast)
apkg build --distro anduinos --suite resolute-addon --arch amd64

# All configured architectures (CI)
apkg build

# Install the result
sudo apt install -y ./bin/anduinos-why-ai_*.deb
```

## Testing the serve mode with Copilot CLI

```sh
why --serve
```

Then in another terminal, copy the `export` lines printed in the banner:

```sh
export COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:8080/v1"
export COPILOT_MODEL="gemma-4-e2b-it-q4_k_m.gguf"
export COPILOT_PROVIDER_MAX_PROMPT_TOKENS=32768
export COPILOT_PROVIDER_MAX_OUTPUT_TOKENS=8192
copilot
```

## CI runner image

The Dockerfile for the CI runner is in:
```
BoxForProart/stage4/images/runner/Dockerfile
```

Make sure it includes **all** the packages listed above. The `install.sh` script
in `~/install.sh` on the ProArt host can patch running containers when new
build dependencies are added between image rebuilds.

## Model download

The model (`gemma-4-E2B-it-Q4_K_M.gguf`, ~3 GB) is downloaded once by
`download.sh` and cached in `deploy/cache/`. It is then copied into the
`obj/` staging tree during `apkg build`.
