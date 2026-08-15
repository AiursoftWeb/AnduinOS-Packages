#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case $ARCH in
    amd64|x86_64) ARCH=amd64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) printf 'SKIP: unsupported test architecture: %s\n' "$ARCH"; exit 0 ;;
esac

MODULE="$ROOT/deploy/$ARCH/anduinos-ghost.so"
[[ -r $MODULE ]] || {
    printf 'SKIP: build %s before Readline lifecycle tests.\n' "$MODULE"
    exit 0
}

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/home" "$TEST_ROOT/package"
cp "$MODULE" "$TEST_ROOT/package/anduinos-ghost.so"

# The lifecycle contract does not depend on semantic predictions. A minimal
# protocol peer keeps the native frontend alive without requiring the Rust
# engine, so this regression test can run immediately after the C build.
cat >"$TEST_ROOT/package/anduinos-quietd" <<'EOF'
#!/usr/bin/env bash
while IFS= read -r request; do
    case $request in
        X) printf 'A\n'; exit 0 ;;
        P) printf 'P\n' ;;
        *) printf 'N\t0\n' ;;
    esac
done
EOF
chmod 755 "$TEST_ROOT/package/anduinos-quietd"

sed -e "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/anduinos-bash-guess-command" >"$TEST_ROOT/package/loader"

cat >"$TEST_ROOT/enabled-bashrc" <<EOF
PS1='READLINE_TEST> '
PS2='READLINE_MORE> '
HISTFILE=/dev/null
stty columns 120 rows 40
export ANDUINOS_QUIETD='$TEST_ROOT/package/anduinos-quietd'
unset ANDUINOS_GUESS_COMMAND
source '$TEST_ROOT/package/loader'
EOF

cat >"$TEST_ROOT/baseline-bashrc" <<'EOF'
PS1='READLINE_TEST> '
PS2='READLINE_MORE> '
HISTFILE=/dev/null
stty columns 120 rows 40
EOF

: >"$TEST_ROOT/inputrc-default"
printf 'set enable-bracketed-paste on\n' >"$TEST_ROOT/inputrc-on"
printf 'set enable-bracketed-paste off\n' >"$TEST_ROOT/inputrc-off"

run_session() {
    local producer=$1 transcript=$2 rcfile=$3 inputrc=$4
    "$producer" | TERM=xterm-256color LC_ALL=C.UTF-8 \
        INPUTRC="$inputrc" HOME="$TEST_ROOT/home" \
        script -qefc "bash --noprofile --rcfile '$rcfile' -i" \
        "$transcript" >/dev/null
}

baseline_input() {
    sleep 0.3
    printf "bind -v >'%s/baseline.vars'\nexit\n" "$TEST_ROOT"
}

enabled_input() {
    sleep 0.3
    printf "bind -v >'%s/enabled.vars'\nexit\n" "$TEST_ROOT"
}

explicit_on_input() {
    sleep 0.3
    printf "bind -v >'%s/explicit-on.vars'\nexit\n" "$TEST_ROOT"
}

explicit_off_input() {
    sleep 0.3
    printf "bind -v >'%s/explicit-off.vars'\nexit\n" "$TEST_ROOT"
}

paste_input() {
    sleep 0.3
    printf '\033[200~printf PASTE_ONE >%s/paste-one\nprintf PASTE_TWO >%s/paste-two\033[201~' \
        "$TEST_ROOT" "$TEST_ROOT"
    sleep 0.2
    printf '\r'
    sleep 0.2
    printf 'exit\n'
}

# Loading from bashrc before the first prompt must preserve Readline's complete
# default variable set, not merely repair one known option.
run_session baseline_input "$TEST_ROOT/baseline.typescript" \
    "$TEST_ROOT/baseline-bashrc" "$TEST_ROOT/inputrc-default"
run_session enabled_input "$TEST_ROOT/enabled.typescript" \
    "$TEST_ROOT/enabled-bashrc" "$TEST_ROOT/inputrc-default"
cmp -s "$TEST_ROOT/baseline.vars" "$TEST_ROOT/enabled.vars" || {
    diff -u "$TEST_ROOT/baseline.vars" "$TEST_ROOT/enabled.vars" >&2 || true
    fail 'loading predictions changed user-selected Readline variables'
}
grep -Fxq 'set enable-bracketed-paste on' "$TEST_ROOT/enabled.vars" ||
    fail 'loading predictions disabled the native bracketed-paste default'
LC_ALL=C grep -aFq $'\033[?2004h' "$TEST_ROOT/enabled.typescript" ||
    fail 'Readline did not negotiate bracketed paste with the terminal'

# Preserve explicit choices in both directions; the fix must correct
# initialization ordering rather than force a distribution preference.
run_session explicit_on_input "$TEST_ROOT/explicit-on.typescript" \
    "$TEST_ROOT/enabled-bashrc" "$TEST_ROOT/inputrc-on"
grep -Fxq 'set enable-bracketed-paste on' "$TEST_ROOT/explicit-on.vars" ||
    fail 'loading predictions overrode an explicit bracketed-paste opt-in'

run_session explicit_off_input "$TEST_ROOT/explicit-off.typescript" \
    "$TEST_ROOT/enabled-bashrc" "$TEST_ROOT/inputrc-off"
grep -Fxq 'set enable-bracketed-paste off' "$TEST_ROOT/explicit-off.vars" ||
    fail 'loading predictions overrode an explicit bracketed-paste opt-out'

run_session paste_input "$TEST_ROOT/paste.typescript" \
    "$TEST_ROOT/enabled-bashrc" "$TEST_ROOT/inputrc-on"
[[ $(<"$TEST_ROOT/paste-one") == PASTE_ONE &&
   $(<"$TEST_ROOT/paste-two") == PASTE_TWO ]] ||
    fail 'native multiline paste did not execute normally after Enter'

printf 'Readline lifecycle checks passed.\n'
