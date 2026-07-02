#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd wget
need_cmd sha256sum coreutils

# Gemma 4 E2B Instruct — Q4_K_M quant (unsloth)
# ~2B effective params, text-only.  Significantly smarter than 0.8B Qwen3.5.
MODEL_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
CACHE_DIR="$SCRIPT_DIR/deploy/cache"
MODEL_FILE="$CACHE_DIR/gemma-4-E2B-it-Q4_K_M.gguf"

# Known SHA-256 of Qwen3.5-0.8B-Q4_K_M.gguf from unsloth.
# Update this whenever the model is refreshed.
KNOWN_SHA256=""
# If KNOWN_SHA256 is empty, skip hash verification (first-time bootstrap).

die() {
    >&2 printf 'download.sh: %s\n' "$1"
    exit 1
}

download_model() {
    mkdir -p "$CACHE_DIR"

    if [[ -f "$MODEL_FILE" ]]; then
        echo "Model already cached: $MODEL_FILE"
        if [[ -n "$KNOWN_SHA256" ]]; then
            local actual
            actual="$(sha256sum "$MODEL_FILE" | awk '{print $1}')"
            if [[ "$actual" != "$KNOWN_SHA256" ]]; then
                die "SHA256 mismatch for cached model.\n  Expected: $KNOWN_SHA256\n  Got:      $actual\n  Delete $MODEL_FILE and retry."
            fi
            echo "SHA256 verified: $actual"
        else
            echo "(no SHA256 pin — skipping verification)"
        fi
        return 0
    fi

    echo "Downloading Gemma 4 E2B GGUF model (~3.1 GB)..."
    echo "URL: $MODEL_URL"
    wget -q --show-progress "$MODEL_URL" -O "$MODEL_FILE.tmp"

    if [[ -n "$KNOWN_SHA256" ]]; then
        local actual
        actual="$(sha256sum "$MODEL_FILE.tmp" | awk '{print $1}')"
        if [[ "$actual" != "$KNOWN_SHA256" ]]; then
            rm -f "$MODEL_FILE.tmp"
            die "SHA256 mismatch after download.\n  Expected: $KNOWN_SHA256\n  Got:      $actual"
        fi
        echo "SHA256 verified: $actual"
    fi

    mv "$MODEL_FILE.tmp" "$MODEL_FILE"
    echo "Model cached at: $MODEL_FILE"
}

# ── Install model into staging areas under obj/ ──────────────────────────────
# The APKG SDK resolves <IncludeFolder> relative to obj/ (flat), while
# <PrebuildCommand> runs before the {suite_arch} staging subdirectory is
# populated.  We copy into BOTH locations so the SDK finds the model.
install_into_staging() {
    # Flat location — this is what <IncludeFolder> resolves against.
    local flat_dir="$SCRIPT_DIR/obj/usr/share/anduinos-why-ai/models"
    mkdir -p "$flat_dir"
    cp "$MODEL_FILE" "$flat_dir/gemma-4-e2b-it-q4_k_m.gguf"
    echo "Installed model into: $flat_dir"

    # Per-suite-arch staging directories (created by the SDK before
    # PrebuildCommand runs).
    local found=0
    shopt -s nullglob
    for stage_dir in "$SCRIPT_DIR"/obj/*; do
        [[ -d "$stage_dir" ]] || continue
        # Skip the flat usr/ dir we just created.
        [[ "$(basename "$stage_dir")" == "usr" ]] && continue
        found=1
        local target_dir="$stage_dir/usr/share/anduinos-why-ai/models"
        mkdir -p "$target_dir"
        cp "$MODEL_FILE" "$target_dir/gemma-4-e2b-it-q4_k_m.gguf"
        echo "Installed model into: $target_dir"
    done
    shopt -u nullglob

    if [[ "$found" -eq 0 ]]; then
        echo "(no per-suite obj subdirectories yet — flat copy is sufficient)"
    fi
}

main() {
    download_model
    install_into_staging
}

main
