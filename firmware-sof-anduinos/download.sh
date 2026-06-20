#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd wget
need_cmd tar

SOF_VERSION="2025.12.2"
SOF_URL="https://github.com/thesofproject/sof-bin/releases/download/v${SOF_VERSION}/sof-bin-${SOF_VERSION}.tar.gz"
CACHE_DIR="$SCRIPT_DIR/deploy/cache"
ARCHIVE_PATH="$CACHE_DIR/sof-bin-${SOF_VERSION}.tar.gz"
EXTRACT_DIR="$CACHE_DIR/sof-bin-${SOF_VERSION}"

die() {
    >&2 printf 'download.sh: %s\n' "$1"
    exit 1
}

download_release() {
    mkdir -p "$CACHE_DIR"

    if [[ ! -f "$ARCHIVE_PATH" ]]; then
        echo "Downloading SOF firmware v${SOF_VERSION}..."
        wget -q "$SOF_URL" -O "$ARCHIVE_PATH"
    fi

    if [[ ! -d "$EXTRACT_DIR" ]]; then
        local tmp_dir archive_root
        tmp_dir="$(mktemp -d "$CACHE_DIR/.extract.XXXXXX")"

        tar -xzf "$ARCHIVE_PATH" -C "$tmp_dir"
        archive_root="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
        [[ -n "$archive_root" ]] || die "Extracted archive is empty: $ARCHIVE_PATH"

        mv "$archive_root" "$EXTRACT_DIR"
        rm -rf "$tmp_dir"
    fi
}

detect_firmware_root() {
    local stage_dir="$1"

    if [[ -d "$stage_dir/usr/lib/firmware/intel" ]]; then
        printf '%s\n' "$stage_dir/usr/lib/firmware/intel"
    elif [[ -d "$stage_dir/lib/firmware/intel" ]]; then
        printf '%s\n' "$stage_dir/lib/firmware/intel"
    else
        mkdir -p "$stage_dir/usr/lib/firmware/intel"
        printf '%s\n' "$stage_dir/usr/lib/firmware/intel"
    fi
}

install_latest_sof() {
    local firmware_root="$1"

    for dir in sof sof-tplg sof-ipc4 sof-ipc4-lib sof-ipc4-tplg sof-ace-tplg; do
        rm -rf "$firmware_root/$dir"
    done

    for dir in sof sof-tplg sof-ipc4 sof-ipc4-lib sof-ipc4-tplg; do
        cp -a "$EXTRACT_DIR/$dir" "$firmware_root/$dir"
    done

    ln -s sof-ipc4-tplg "$firmware_root/sof-ace-tplg"
}

main() {
    local stage_dir found_stage=0

    download_release

    shopt -s nullglob
    for stage_dir in "$SCRIPT_DIR"/obj/*; do
        [[ -d "$stage_dir" ]] || continue
        found_stage=1
        install_latest_sof "$(detect_firmware_root "$stage_dir")"
    done
    shopt -u nullglob

    [[ "$found_stage" -eq 1 ]] || die "Missing upstream staging directory under: $SCRIPT_DIR/obj"
}

main
