#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Updating this trust anchor requires a reviewed source change.
readonly key_sha256=4aee607db52c652290045b0b77c30d62adecff7bf3f6d801a66d38f9db5693df
readonly key_url=https://offline-update-packages.anduinos.com/sign-cert.gpg
mkdir -p deploy
temporary=$(mktemp deploy/.signing-key.XXXXXX)
trap 'rm -f "$temporary"' EXIT
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 120 --retry 3 --output "$temporary" "$key_url"
if ! printf '%s  %s\n' "$key_sha256" "$temporary" | sha256sum --check --status; then
    echo 'Official offline-update signing key checksum mismatch; refusing to build.' >&2
    exit 1
fi
chmod 0644 "$temporary"
mv "$temporary" deploy/anduinos-offline-updates.gpg
