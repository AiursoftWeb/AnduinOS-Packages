#!/bin/bash
# Build-time public model acquisition only; runtime never downloads a VAD model.
set -euo pipefail
vad_revision=c5c26827b67dfd053856f92e824e14fdcc123daf
vad_sha256=2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987
vad_path=obj/models/ggml-silero-v6.2.0.bin
mkdir -p obj/models
if [ -f "$vad_path" ] && echo "$vad_sha256  $vad_path" | sha256sum --check --status; then
    exit 0
fi
curl --fail --location --retry 3 --connect-timeout 20 --max-time 180 \
    "https://huggingface.co/ggml-org/whisper-vad/resolve/$vad_revision/ggml-silero-v6.2.0.bin" \
    --output "$vad_path.part"
echo "$vad_sha256  $vad_path.part" | sha256sum --check --status
mv "$vad_path.part" "$vad_path"
