#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case $ARCH in
    amd64|x86_64) ARCH=amd64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) printf 'SKIP: unsupported test architecture: %s\n' "$ARCH"; exit 0 ;;
esac

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

MODULE="$ROOT/deploy/$ARCH/anduinos-ghost.so"
DAEMON="$ROOT/deploy/$ARCH/anduinos-quietd"
[[ -r $MODULE && -x $DAEMON ]] || {
    printf 'SKIP: build native frontend and engine for %s first.\n' "$ARCH"
    exit 0
}

TEST_ROOT="$(mktemp -d)"
if [[ ${ANDUINOS_TEST_KEEP:-0} == 0 ]]; then
    trap 'rm -rf -- "$TEST_ROOT"' EXIT
else
    printf 'Keeping native test files in %s\n' "$TEST_ROOT"
fi
mkdir -p "$TEST_ROOT/home/bin" "$TEST_ROOT/package/bin"
cp "$MODULE" "$TEST_ROOT/package/anduinos-ghost.so"
cp "$DAEMON" "$TEST_ROOT/package/anduinos-quietd"

sed \
    -e "s|/usr/share/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    -e "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/anduinos-bash-guess-command" >"$TEST_ROOT/package/loader"

cat >"$TEST_ROOT/home/bin/sudo" <<EOF
#!/usr/bin/env bash
if [[ \${1-} == -n && \${2-} == docker ]]; then
    shift 2
    exec docker "\$@"
fi
printf '%s\n' "\$*" >'$TEST_ROOT/sudo.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/sudo"

cat >"$TEST_ROOT/home/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ ${1-} == container && ${2-} == ls ]]; then
    printf '59ab75d539d4\tkind_bassi\tubuntu:26.04\n'
    if [[ -f ${ANDUINOS_TEST_MULTIPLE_MARKER:-/nonexistent} ]]; then
        printf '349eb1bc73fb\tjovial_ptolemy\tmarktohtml:latest\n'
    fi
fi
EOF
chmod 755 "$TEST_ROOT/home/bin/docker"

cat >"$TEST_ROOT/bashrc" <<EOF
PS1='NATIVE_TEST> '
PS2='NATIVE_MORE> '
stty columns 120 rows 40
PATH='$TEST_ROOT/home/bin':\$PATH
export ANDUINOS_QUIETD='$TEST_ROOT/package/anduinos-quietd'
export ANDUINOS_TEST_MULTIPLE_MARKER='$TEST_ROOT/multiple-containers'
ANDUINOS_GUESS_STATIC=0
unset ANDUINOS_GUESS_COMMAND
source '$TEST_ROOT/package/loader'
EOF

run_session() {
    local producer=$1 transcript=$2
    "$producer" | ANDUINOS_GUESS_COMMAND=0 TERM=xterm-256color \
        HOME="$TEST_ROOT/home" \
        script -qefc "bash --noprofile --rcfile '$TEST_ROOT/bashrc' -i" \
        "$transcript" >/dev/null
}

accept_workflow_input() {
    sleep 0.5
    printf 'sudo apt update\n'
    sleep 0.2
    printf 'sudo apt up'
    sleep 0.2
    printf '\033[C\nexit\n'
}

apt_skeleton_input() {
    sleep 0.5
    printf 'sudo apt '
    sleep 0.2
    printf '\033[C\nexit\n'
}

enter_native_input() {
    sleep 0.5
    printf 'sudo apt up'
    sleep 0.2
    printf '\r'
    sleep 0.2
    printf 'exit\n'
}

docker_input() {
    sleep 0.5
    printf 'sudo docker ps\n'
    sleep 0.35
    printf 'sudo docker exec -it '
    sleep 0.2
    printf '\033[C\nexit\n'
}

docker_skeleton_input() {
    sleep 0.5
    printf 'sudo docker '
    sleep 0.2
    printf '\033[C\nexit\n'
}

git_skeleton_input() {
    sleep 0.5
    printf 'sudo git'
    sleep 0.2
    printf '\033[C\nexit\n'
}

git_checkout_input() {
    sleep 0.5
    printf 'sudo git che'
    sleep 0.2
    printf '\033[C\nexit\n'
}

docker_ambiguous_input() {
    sleep 0.5
    touch "$TEST_ROOT/multiple-containers"
    printf 'sudo docker ps\n'
    sleep 0.35
    printf 'sudo docker logs -f '
    sleep 0.2
    printf '\033[C\nexit\n'
}

paste_input() {
    sleep 0.5
    printf '\033[200~printf PASTE_ONE >%s/paste-one\nprintf PASTE_TWO >%s/paste-two\033[201~' \
        "$TEST_ROOT" "$TEST_ROOT"
    sleep 0.25
    printf '\r'
    sleep 0.25
    printf 'exit\n'
}

learn_history_input() {
    sleep 0.5
    printf "printf PERSONAL_MEMORY >'%s/personal-memory'\n" "$TEST_ROOT"
    sleep 0.25
    printf 'exit\n'
}

recall_history_input() {
    sleep 0.5
    printf 'printf PERSONAL_'
    sleep 0.2
    printf '\033[C\nexit\n'
}

path_input() {
    sleep 0.7
    printf 'cat READ'
    sleep 0.2
    printf '\033[C\nexit\n'
}

run_session accept_workflow_input "$TEST_ROOT/workflow.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt upgrade' ]] ||
    fail 'Right Arrow did not accept the apt workflow suggestion'
LC_ALL=C grep -aFq $'\033[90m' "$TEST_ROOT/workflow.typescript" ||
    fail 'the suggestion was accepted but never rendered as ghost text'

run_session apt_skeleton_input "$TEST_ROOT/apt-skeleton.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt update' ]] ||
    fail 'apt command skeleton was silent'

run_session enter_native_input "$TEST_ROOT/enter.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt up' ]] ||
    fail 'Enter accepted ghost text instead of executing the typed line'

run_session docker_input "$TEST_ROOT/docker.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'docker exec -it kind_bassi' ]] ||
    fail 'live Docker context was not suggested'

run_session docker_skeleton_input "$TEST_ROOT/docker-skeleton.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'docker ps' ]] ||
    fail 'Docker command skeleton was silent'

run_session git_skeleton_input "$TEST_ROOT/git-skeleton.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'git status' ]] ||
    fail 'Git command skeleton was silent'

run_session git_checkout_input "$TEST_ROOT/git-checkout.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'git checkout' ]] ||
    fail 'human-facing Git action did not outrank plumbing commands'

run_session docker_ambiguous_input "$TEST_ROOT/docker-ambiguous.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'docker logs -f' ]] ||
    fail 'Docker listing order was incorrectly treated as user intent'

run_session paste_input "$TEST_ROOT/paste.typescript"
[[ $(<"$TEST_ROOT/paste-one") == PASTE_ONE &&
   $(<"$TEST_ROOT/paste-two") == PASTE_TWO ]] ||
    fail 'native multiline paste did not execute normally after Enter'
if LC_ALL=C tr -d '\r' <"$TEST_ROOT/paste.typescript" | grep -Eqi \
    -- '-- MULTILINE --|RET or C-m:|progress|updating tput cache|ble\.sh'; then
    fail 'line-editor UI leaked into multiline paste'
fi

run_session learn_history_input "$TEST_ROOT/history-learn.typescript"
history_file="$TEST_ROOT/home/.local/state/anduinos-bash-guess-command/history-v1"
[[ -s $history_file && $(stat -c %a "$history_file") == 600 ]] ||
    fail 'personal history was not persisted privately'
find "$TEST_ROOT" -maxdepth 1 -type f -name personal-memory -delete
run_session recall_history_input "$TEST_ROOT/history-recall.typescript"
[[ $(<"$TEST_ROOT/personal-memory") == PERSONAL_MEMORY ]] ||
    fail 'a new Bash session did not recall its local personal command'

run_session path_input "$TEST_ROOT/path.typescript"
LC_ALL=C tr -d '\r' <"$TEST_ROOT/path.typescript" | \
    grep -q 'anduinos-bash-guess-command' ||
    fail 'current-directory snapshot did not complete README.md'

printf 'Native Readline interaction checks passed.\n'
