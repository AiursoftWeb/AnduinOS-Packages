#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

bash -n "$ROOT/download.sh" "$ROOT/assets/carapace-wrapper" \
    "$ROOT/assets/anduinos-bash-guess-command" \
    "$ROOT/assets/anduinos-guess-context.bash" "$ROOT/tests/test-interactive.sh" \
    "$ROOT/tests/test-offline.sh" "$ROOT/tests/test-performance.sh"

grep -q '<PackageName>anduinos-bash-guess-command</PackageName>' \
    "$ROOT/anduinos-bash-guess-command.aosproj" || fail 'wrong package name'
grep -q 'Target="/etc/bash_completion.d/anduinos-bash-guess-command"' \
    "$ROOT/anduinos-bash-guess-command.aosproj" || fail 'standard Bash loader is not package-owned'
grep -q 'Target="/usr/share/anduinos-bash-guess-command/anduinos-guess-context.bash"' \
    "$ROOT/anduinos-bash-guess-command.aosproj" || fail 'context source is not packaged'

grep -q "builtin read -r -d '' nospace data" \
    "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'Carapace read is not protected from ble.sh interception'
grep -q '_anduinos_guess_normalize_carapace_assignment' \
    "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'Carapace assignment candidates are not normalized for ble.sh'
grep -q '_anduinos_guess_enable_dash_autocomplete' \
    "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'dash option prefixes do not trigger automatic completion'
grep -q 'ANDUINOS_GUESS_TRIM_PASTE_NEWLINE' \
    "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'terminal selection newlines are not handled'

# shellcheck disable=SC2016 # the literal variable must appear in download.sh
grep -q 'rm -rf -- "$destination"' "$ROOT/download.sh" ||
    fail 'ble.sh staging is not cleaned before packaging'

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
grep -q "completion_timeout_seconds" "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace millisecond timeout is not converted to seconds'
grep -q 'unshare --user --map-root-user --net' "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace is not isolated from the network'
if grep -Eq '^[[:space:]]*(exec )?timeout .*([0-9]+ms)' "$ROOT/assets/carapace-wrapper"; then
    fail 'GNU timeout does not accept millisecond duration suffixes'
fi
grep -q "tr -d" "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace output is not filtered'

printf 'All package integration checks passed.\n'

bash "$ROOT/tests/test-interactive.sh"
bash "$ROOT/tests/test-offline.sh"
bash "$ROOT/tests/test-performance.sh"
