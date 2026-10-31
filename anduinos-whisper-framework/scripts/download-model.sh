#!/bin/bash
set -euo pipefail

model_url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin?download=true"
model_sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
model_path="obj/models/ggml-base.bin"

mkdir -p "$(dirname "$model_path")"
if [ -f "$model_path" ] && echo "$model_sha256  $model_path" | sha256sum --check --status; then
    exit 0
fi

partial_path="${model_path}.part"
curl --fail --location --retry 3 --continue-at - --output "$partial_path" "$model_url"
echo "$model_sha256  $partial_path" | sha256sum --check --status
mv "$partial_path" "$model_path"

