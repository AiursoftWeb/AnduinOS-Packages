#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

bash -n "$ROOT/download.sh" "$ROOT/assets/carapace-wrapper" \
    "$ROOT/assets/anduinos-bash-guess-command"

grep -q '<PackageName>anduinos-bash-guess-command</PackageName>' \
    "$ROOT/anduinos-bash-guess-command.aosproj" || fail 'wrong package name'
grep -q 'Target="/etc/bash_completion.d/anduinos-bash-guess-command"' \
    "$ROOT/anduinos-bash-guess-command.aosproj" || fail 'standard Bash loader is not package-owned'

for setting in highlight_syntax highlight_filename highlight_variable \
    complete_menu_color complete_menu_color_match exec_errexit_mark; do
    grep -q "^bleopt $setting=$" "$ROOT/assets/anduinos-bash-guess-command" ||
        fail "$setting is not disabled"
done

# Disabling must return before touching packaged resources, even when they are
# absent from the test host.
bash --noprofile --norc -ic \
    'set -u; ANDUINOS_GUESS_COMMAND=0; source "$1"' bash \
    "$ROOT/assets/anduinos-bash-guess-command" 2>/dev/null

grep -q 'timeout --signal=TERM' "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace has no hard timeout'
grep -q "tr -d" "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace output is not filtered'

printf 'All package integration checks passed.\n'
