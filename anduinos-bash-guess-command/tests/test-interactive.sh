#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "$ARCH" in
    amd64|x86_64) ARCH=amd64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) printf 'SKIP: unsupported test architecture: %s\n' "$ARCH"; exit 0 ;;
esac

BLESH="$ROOT/deploy/$ARCH/blesh/ble.sh"
CARAPACE="$ROOT/deploy/$ARCH/carapace"
if [[ ! -r "$BLESH" || ! -x "$CARAPACE" ]]; then
    printf 'SKIP: run bash download.sh %s before interactive tests.\n' "$ARCH"
    exit 0
fi

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

TEST_ROOT="$(mktemp -d)"
if [[ ${ANDUINOS_TEST_KEEP:-0} == 0 ]]; then
    trap 'rm -rf -- "$TEST_ROOT"' EXIT
else
    printf 'Keeping interactive test files in %s\n' "$TEST_ROOT"
fi
mkdir -p "$TEST_ROOT/home" "$TEST_ROOT/package/bin" "$TEST_ROOT/package/blesh"
cp -a "$(dirname "$BLESH")/." "$TEST_ROOT/package/blesh/"
ln -s "$CARAPACE" "$TEST_ROOT/package/carapace"
ln -s "$ROOT/assets/anduinos-guess-context.bash" \
    "$TEST_ROOT/package/anduinos-guess-context.bash"

# Stage the package-owned files under a private prefix. Production paths remain
# hard-coded in the shipped files; only this disposable test copy is rewritten.
sed \
    -e "s|/usr/share/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    -e "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/anduinos-bash-guess-command" >"$TEST_ROOT/package/loader"
sed \
    -e "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/carapace-wrapper" >"$TEST_ROOT/package/bin/carapace"
chmod 755 "$TEST_ROOT/package/bin/carapace"

mkdir -p "$TEST_ROOT/home/bin"
cat >"$TEST_ROOT/home/bin/docker" <<EOF
#!/usr/bin/env bash
if [[ \${1-} == container && \${2-} == ls ]]; then
    printf '0ad123456789\\tmysql-db\\tmysql:8\\n7be987654321\\tweb\\tnginx:latest\\n'
elif [[ \${1-} == ps ]]; then
    printf 'ps\\n' >'$TEST_ROOT/docker.action'
    printf '0ad123456789 mysql-db mysql:8\\n7be987654321 web nginx:latest\\n'
else
    printf '%s\\n' "\$*" >'$TEST_ROOT/docker.args'
fi
EOF
chmod 755 "$TEST_ROOT/home/bin/docker"
cat >"$TEST_ROOT/home/bin/curl" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "\$*" >'$TEST_ROOT/curl.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/curl"
cat >"$TEST_ROOT/home/bin/ps" <<'EOF'
#!/usr/bin/env bash
printf '4242 mysqld\n7331 nginx\n'
EOF
chmod 755 "$TEST_ROOT/home/bin/ps"
cat >"$TEST_ROOT/home/bin/sudo" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "\$*" >'$TEST_ROOT/sudo.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/sudo"
cat >"$TEST_ROOT/home/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ $* == list-units* ]]; then
    printf 'docker.service loaded active running Docker Engine\n'
    printf 'ssh.service loaded active running OpenSSH Server\n'
fi
EOF
chmod 755 "$TEST_ROOT/home/bin/systemctl"
cat >"$TEST_ROOT/home/bin/git" <<EOF
#!/usr/bin/env bash
if [[ \${1-} == for-each-ref ]]; then
    printf 'feature-login\\nmain\\n'
elif [[ \${1-} == switch ]]; then
    printf '%s\\n' "\$*" >'$TEST_ROOT/git.args'
else
    exec /usr/bin/git "\$@"
fi
EOF
chmod 755 "$TEST_ROOT/home/bin/git"
cat >"$TEST_ROOT/home/bin/apt" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "\$*" >'$TEST_ROOT/apt.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/apt"
printf 'ANDUINOS_FILE_OK\n' >"$TEST_ROOT/quiet-file.txt"
printf 'ANDUINOS_DD_OK\n' >"$TEST_ROOT/dd-input.img"
mkdir -p "$TEST_ROOT/dd-many"
printf 'first\n' >"$TEST_ROOT/dd-many/alpha.img"
printf 'second\n' >"$TEST_ROOT/dd-many/beta.img"

cat >"$TEST_ROOT/bashrc" <<EOF
PS1='ANDUINOS_TEST> '
PS2='ANDUINOS_MORE> '
PATH='$TEST_ROOT/home/bin':\$PATH
HISTFILE='$TEST_ROOT/home/.bash_history'
HISTSIZE=500
HISTFILESIZE=500
set +o history
history -c
for _anduinos_test_index in {1..250}; do
    history -s "printf unrelated-\$_anduinos_test_index"
done
unset _anduinos_test_index
history -s "touch '$TEST_ROOT/history.accepted'"
history -s "printf frequent >'$TEST_ROOT/rank.result'"
history -s "printf frequent >'$TEST_ROOT/rank.result'"
history -s "printf frequent >'$TEST_ROOT/rank.result'"
history -s "printf frequent >'$TEST_ROOT/rank.result'"
history -s "printf accidental >'$TEST_ROOT/rank.result'"
history -s "curl --token supersecret"
history -s "docker rm web"
history -s "docker rm web"
history -s "docker rm web"
history -s "docker rm web"
history -s "docker ps"
history -s "true && touch '$TEST_ROOT/enter-must-not-accept'"
history -s 'echo malicious-\$(touch $TEST_ROOT/history-subscript-injection)'
set -o history
source '$TEST_ROOT/package/loader'
EOF

run_session() {
    local input_function="$1" transcript="$2"
    "$input_function" | TERM=xterm-256color HOME="$TEST_ROOT/home" \
        script -qefc "bash --noprofile --rcfile '$TEST_ROOT/bashrc' -i" \
        "$transcript" >/dev/null
}

run_session_without_context() {
    local input_function="$1" transcript="$2"
    "$input_function" | ANDUINOS_GUESS_CONTEXT=0 TERM=xterm-256color \
        HOME="$TEST_ROOT/home" \
        script -qefc "bash --noprofile --rcfile '$TEST_ROOT/bashrc' -i" \
        "$transcript" >/dev/null
}

history_input() {
    sleep 1.5
    printf "touch '%s/hist" "$TEST_ROOT"
    sleep 0.15
    printf '\033[C\nexit\n'
}

static_input() {
    sleep 1.5
    printf 'git statu'
    sleep 0.5
    printf '\033[C\nexit\n'
}

context_id_input() {
    sleep 1.5
    printf 'docker exec -it 0ad'
    sleep 0.25
    printf '\033[C\nexit\n'
}

context_session_input() {
    sleep 1.5
    printf 'docker ps | grep mysql\n'
    sleep 0.15
    printf 'docker exec -it '
    sleep 0.25
    printf '\033[C\nexit\n'
}

ranked_history_input() {
    sleep 1.5
    printf 'printf '
    sleep 0.15
    printf '\033[C\nexit\n'
}

sensitive_input() {
    sleep 1.5
    printf 'curl --token '
    sleep 0.15
    printf '\033[C\nexit\n'
}

process_session_input() {
    sleep 1.5
    printf 'ps aux | grep mysqld\n'
    sleep 0.15
    printf 'sudo kill '
    sleep 0.25
    printf '\033[C\nexit\n'
}

service_session_input() {
    sleep 1.5
    printf 'systemctl list-units | grep docker\n'
    sleep 0.15
    printf 'sudo systemctl status '
    sleep 0.25
    printf '\033[C\nexit\n'
}

git_context_input() {
    sleep 1.5
    printf 'git switch fea'
    sleep 0.25
    printf '\033[C\nexit\n'
}

danger_rank_input() {
    sleep 1.5
    printf 'docker '
    sleep 0.15
    printf '\033[C\nexit\n'
}

apt_static_input() {
    sleep 1.5
    printf 'apt instal'
    sleep 0.5
    printf '\033[C\nexit\n'
}

file_input() {
    sleep 1.5
    printf 'cat %s/quiet-f' "$TEST_ROOT"
    sleep 0.5
    printf '\033[C\nexit\n'
}

dd_assignment_input() {
    sleep 1.5
    printf 'sudo dd if=%s/dd-inp' "$TEST_ROOT"
    sleep 0.5
    printf '\033[C\nexit\n'
}

dd_ambiguous_assignment_input() {
    sleep 1.5
    printf 'sudo dd if=%s/dd-many/' "$TEST_ROOT"
    sleep 0.5
    printf '\033[C\nexit\n'
}

enter_safety_input() {
    sleep 1.5
    printf 'true'
    sleep 0.15
    # A terminal Enter key sends CR (C-m). LF is C-j, which ble.sh deliberately
    # binds to accept-and-execute and is therefore not equivalent here.
    printf '\r'
    sleep 0.2
    printf 'exit\n'
}

history_injection_input() {
    sleep 1.5
    printf 'echo malicious-'
    sleep 0.15
    printf '\003exit\n'
}

broad_history_input() {
    sleep 1.5
    printf 'printf unrelated'
    # Includes ble.sh's debounce and PTY scheduling, not just ranking time.
    sleep 0.25
    printf '\033[C\nexit\n'
}

run_session history_input "$TEST_ROOT/history.typescript"
[[ -e "$TEST_ROOT/history.accepted" ]] || fail 'history ghost text was not accepted'

run_session ranked_history_input "$TEST_ROOT/ranked-history.typescript"
[[ $(<"$TEST_ROOT/rank.result") == frequent ]] ||
    fail 'frequent history did not outrank an accidental recent command'

rm -f "$TEST_ROOT/rank.result"
run_session_without_context ranked_history_input \
    "$TEST_ROOT/ranked-history-without-context.typescript"
[[ $(<"$TEST_ROOT/rank.result") == frequent ]] ||
    fail 'disabling live context also disabled ranked history'

run_session sensitive_input "$TEST_ROOT/sensitive.typescript"
[[ $(<"$TEST_ROOT/curl.args") == '--token' ]] ||
    fail 'a sensitive history value leaked into ghost text'

run_session danger_rank_input "$TEST_ROOT/danger-rank.typescript"
[[ $(<"$TEST_ROOT/docker.action") == ps ]] ||
    fail 'a destructive history command outranked a safe alternative'

run_session process_session_input "$TEST_ROOT/context-process.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'kill 4242' ]] ||
    fail 'previous process filter was not used as typed session context'

run_session service_session_input "$TEST_ROOT/context-service.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'systemctl status docker.service' ]] ||
    fail 'previous service filter was not used as typed session context'

run_session git_context_input "$TEST_ROOT/context-git.typescript"
[[ $(<"$TEST_ROOT/git.args") == 'switch feature-login' ]] ||
    fail 'current repository branch was not completed'

run_session static_input "$TEST_ROOT/static.typescript"
LC_ALL=C tr -d '\r' <"$TEST_ROOT/static.typescript" | \
    grep -Eq 'On branch|HEAD detached' || fail 'Carapace ghost text was not accepted'

run_session apt_static_input "$TEST_ROOT/static-apt.typescript"
[[ $(<"$TEST_ROOT/apt.args") == install ]] ||
    fail 'Carapace package-manager specification was not accepted'

run_session file_input "$TEST_ROOT/static-file.typescript"
LC_ALL=C tr -d '\r' <"$TEST_ROOT/static-file.typescript" | \
    grep -q ANDUINOS_FILE_OK || fail 'filesystem ghost text was not accepted'

run_session dd_assignment_input "$TEST_ROOT/static-dd-assignment.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == "dd if=$TEST_ROOT/dd-input.img" ]] ||
    fail 'Carapace and ble.sh duplicated a dd assignment prefix'
if LC_ALL=C tr -d '\r' <"$TEST_ROOT/static-dd-assignment.typescript" | \
    grep -q '\[if=if='; then
    fail 'replacement-style dd assignment leaked into ghost text'
fi

run_session dd_ambiguous_assignment_input \
    "$TEST_ROOT/static-dd-ambiguous.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == "dd if=$TEST_ROOT/dd-many/" ]] ||
    fail 'an arbitrary dd path was suggested for ambiguous input'

run_session enter_safety_input "$TEST_ROOT/enter-safety.typescript"
[[ ! -e "$TEST_ROOT/enter-must-not-accept" ]] ||
    fail 'Enter accepted and executed ghost text automatically'

run_session history_injection_input "$TEST_ROOT/history-injection.typescript"
[[ ! -e "$TEST_ROOT/history-subscript-injection" ]] ||
    fail 'history text was evaluated while ranking candidates'

run_session broad_history_input "$TEST_ROOT/broad-history.typescript"
LC_ALL=C tr -d '\r' <"$TEST_ROOT/broad-history.typescript" | \
    grep -q 'unrelated-250' ||
    fail 'bounded broad-prefix history ranking missed its latency window'

run_session context_id_input "$TEST_ROOT/context-id.typescript"
[[ $(<"$TEST_ROOT/docker.args") == 'exec -it 0ad123456789' ]] ||
    fail 'typed Docker container ID was not completed'

rm -f "$TEST_ROOT/docker.args"
run_session context_session_input "$TEST_ROOT/context-session.typescript"
[[ $(<"$TEST_ROOT/docker.args") == 'exec -it mysql-db' ]] ||
    fail 'previous Docker filter was not used as typed session context'

printf 'All interactive suggestion checks passed.\n'
