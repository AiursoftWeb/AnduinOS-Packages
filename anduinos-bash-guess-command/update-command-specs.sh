#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH=${1:-amd64}
CARAPACE="$ROOT/deploy/$ARCH/carapace"
POLICY="$ROOT/engine/specs/commands.tsv"
OUTPUT="$ROOT/engine/specs/generated-subcommands.tsv"

[[ -x $CARAPACE ]] || {
    printf 'update-command-specs.sh: missing %s; run download.sh %s first\n' "$CARAPACE" "$ARCH" >&2
    exit 1
}

temporary_root="$(mktemp -d)"
temporary="$temporary_root/generated"
build_home="$temporary_root/home"
mkdir -p "$build_home"
trap 'rm -rf -- "$temporary_root"' EXIT
printf '# Generated from Carapace 1.7.3; do not edit by hand.\n' >"$temporary"

while IFS=$'\t' read -r command default _legacy; do
    [[ -n $command && $command != \#* ]] || continue
    # This index is for verb-shaped root subcommands. Option/path defaults are
    # deliberately retained in the compact policy overlay: root completion for
    # those commands is dynamic and may contain build-host files or users.
    [[ $default != - && $default != -* && $default != .* ]] || continue
    raw="$(
        cd "$build_home"
        timeout 3s env -i HOME="$build_home" PATH=/nonexistent \
            TMPDIR="${TMPDIR:-/tmp}" CARAPACE_BRIDGES='' \
            "$CARAPACE" "$command" bash "$command" '' 2>/dev/null || true
    )"
    payload=${raw#*$'\001'}
    [[ $payload != "$raw" ]] || continue
    actions="$({
        printf '%s\n' "$payload" |
            LC_ALL=C sed '/^[[:alnum:]_.:+@/-][[:alnum:]_.:+@/-]*$/!d' |
            sed '/^ERR$/d; /^_$/d; /^[0-9][0-9.]*$/d' |
            awk 'length($0) <= 96 && !seen[$0]++'
    } | paste -sd, -)"
    [[ -n $actions ]] || continue
    case ",$actions," in
        *,"$default",*) ;;
        *) continue ;;
    esac
    printf '%s\t%s\n' "$command" "$actions" >>"$temporary"
done <"$POLICY"

mv "$temporary" "$OUTPUT"
rm -rf -- "$temporary_root"
trap - EXIT
printf 'Generated %s command specifications in %s\n' \
    "$(($(wc -l <"$OUTPUT") - 1))" "$OUTPUT"
