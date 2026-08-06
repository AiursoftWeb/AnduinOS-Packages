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
mkdir -p "$TEST_ROOT/home/bin" "$TEST_ROOT/home/.ssh" "$TEST_ROOT/package/bin"
cp "$MODULE" "$TEST_ROOT/package/anduinos-ghost.so"
cp "$DAEMON" "$TEST_ROOT/package/anduinos-quietd-engine"

cat >"$TEST_ROOT/package/anduinos-quietd" <<EOF
#!/usr/bin/env bash
exec '$TEST_ROOT/package/anduinos-quietd-engine' --fixture-bin-dir '$TEST_ROOT/home/bin'
EOF
chmod 755 "$TEST_ROOT/package/anduinos-quietd"

sed \
    -e "s|/usr/share/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    -e "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/anduinos-bash-guess-command" >"$TEST_ROOT/package/loader"

cat >"$TEST_ROOT/home/bin/sudo" <<EOF
#!/usr/bin/env bash
if [[ \${1-} == -n && \${2##*/} == docker ]]; then
    docker=\$2
    shift 2
    exec "\$docker" "\$@"
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

cat >"$TEST_ROOT/home/bin/apt-cache" <<'EOF'
#!/usr/bin/env bash
[[ $* == '--no-generate pkgnames' ]] || exit 1
printf '%s\n' bash bat bmon borgbackup btop build-essential
EOF
chmod 755 "$TEST_ROOT/home/bin/apt-cache"

cat >"$TEST_ROOT/home/bin/dpkg-query" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' bash build-essential
EOF
chmod 755 "$TEST_ROOT/home/bin/dpkg-query"

cat >"$TEST_ROOT/home/bin/nativecmd" <<EOF
#!/usr/bin/env bash
printf '%s' "\$*" >'$TEST_ROOT/native-tab.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/nativecmd"

cat >"$TEST_ROOT/home/bin/ssh" <<EOF
#!/usr/bin/env bash
printf '%s' "\$*" >'$TEST_ROOT/ssh.args'
EOF
chmod 755 "$TEST_ROOT/home/bin/ssh"
cat >"$TEST_ROOT/home/.ssh/config" <<'EOF'
Host production-api
    HostName 192.0.2.10
EOF
printf 'EXPLICIT_PATH_PREFIX\n' >"$TEST_ROOT/home/.bash_alpha"
mkdir -p "$TEST_ROOT/home/demo-directory"

cat >"$TEST_ROOT/bashrc" <<EOF
PS1='NATIVE_TEST> '
PS2='NATIVE_MORE> '
stty columns 120 rows 40
cd '$ROOT'
PATH='$TEST_ROOT/home/bin':\$PATH
export ANDUINOS_QUIETD='$TEST_ROOT/package/anduinos-quietd'
export ANDUINOS_TEST_MULTIPLE_MARKER='$TEST_ROOT/multiple-containers'
export ANDUINOS_GUESS_PERSIST=1
unset ANDUINOS_GUESS_COMMAND
exec 9>'$TEST_ROOT/inherited-fd-sentinel'
_anduinos_native_tab_complete() { COMPREPLY=(native-tab-result); }
complete -F _anduinos_native_tab_complete nativecmd
source '$TEST_ROOT/package/loader'
source '$TEST_ROOT/package/loader'
EOF

cp "$TEST_ROOT/bashrc" "$TEST_ROOT/late-disabled-bashrc"
cat >>"$TEST_ROOT/late-disabled-bashrc" <<'EOF'
# Match the OOBE-managed block at the end of a normal Ubuntu .bashrc, after
# bash-completion has already sourced the package loader.
export ANDUINOS_GUESS_COMMAND=0
EOF

run_session_with_rcfile() {
    local producer=$1 transcript=$2 rcfile=$3
    "$producer" | ANDUINOS_GUESS_COMMAND=0 TERM=xterm-256color \
        HOME="$TEST_ROOT/home" \
        script -qefc "bash --noprofile --rcfile '$rcfile' -i" \
        "$transcript" >/dev/null
}

run_session() {
    run_session_with_rcfile "$1" "$2" "$TEST_ROOT/bashrc"
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

apt_package_input() {
    sleep 0.5
    printf 'sudo apt install b'
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

docker_mind_reading_input() {
    sleep 0.5
    printf 'sudo docker ps\n'
    sleep 0.35
    printf 'sudo docker e'
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

explicit_current_path_input() {
    sleep 0.5
    printf 'cd %s\n' "$TEST_ROOT/home"
    sleep 0.3
    printf 'cat ./.bash_a'
    sleep 0.2
    printf '\033[C\nexit\n'
}

ls_option_path_input() {
    sleep 0.5
    printf 'cd %s\n' "$TEST_ROOT/home"
    sleep 0.3
    printf 'ls -ashl ./de'
    sleep 0.2
    printf '\033[C >%s/ls-option-output\nexit\n' "$TEST_ROOT"
}

native_tab_input() {
    sleep 0.5
    printf 'nativecmd nat\t\nexit\n'
}

transition_input() {
    sleep 0.5
    printf 'printf CONTEXT_ONE >%s/context-one\n' "$TEST_ROOT"
    sleep 0.15
    printf 'printf CONTEXT_TWO >%s/context-two\n' "$TEST_ROOT"
    sleep 0.15
    printf 'printf CONTEXT_ONE >%s/context-one\n' "$TEST_ROOT"
    sleep 0.15
    printf 'printf CONTEXT_'
    sleep 0.2
    printf '\033[C\nexit\n'
}

created_directory_input() {
    sleep 0.5
    printf 'mkdir %s/smart-directory\n' "$TEST_ROOT"
    sleep 0.3
    printf 'cd %s/sm' "$TEST_ROOT"
    sleep 0.2
    printf '\033[C\n'
    sleep 0.15
    printf 'pwd >%s/artifact-pwd\nexit\n' "$TEST_ROOT"
}

ssh_host_input() {
    sleep 0.7
    printf 'ssh prod'
    sleep 0.2
    printf '\033[C\nexit\n'
}

dd_input_path_input() {
    sleep 0.6
    printf 'sudo dd if=/'
    sleep 0.2
    printf '\033[C\nexit\n'
}

dd_empty_output_input() {
    sleep 0.6
    printf 'sudo dd of='
    sleep 0.2
    printf '\033[C\nexit\n'
}

destructive_history_input() {
    sleep 0.6
    printf 'sudo rm -rf /nonexistent-anduinos-ghost-test\n'
    sleep 0.2
    printf 'sudo rm -'
    sleep 0.2
    printf '\033[C\nexit\n'
}

loader_lifecycle_input() {
    sleep 0.5
    printf 'declare -p PROMPT_COMMAND >%s/prompt-command\n' "$TEST_ROOT"
    printf 'helper=$(pgrep -P $$ -x anduinos-quietd); [[ -n $helper && ! -e /proc/$helper/fd/9 ]] && printf CLOSED >%s/fd-hygiene\n' "$TEST_ROOT"
    sleep 0.2
    printf 'exit\n'
}

helper_recovery_input() {
    sleep 0.5
    printf 'kill -KILL "$(pgrep -P $$ -x anduinos-quietd)"\n'
    sleep 0.5
    printf 'sudo git che'
    sleep 0.2
    printf '\033[C\nexit\n'
}

disabled_prediction_probe_input() {
    sleep 0.5
    printf 'helper=$(pgrep -P $$ -x anduinos-quietd || :); [[ -z $helper ]] && printf STOPPED >%s/helper-stopped\n' "$TEST_ROOT"
    printf 'sudo docker p'
    sleep 0.2
    printf '\033[C\nexit\n'
}

runtime_disable_input() {
    sleep 0.5
    printf 'export ANDUINOS_GUESS_COMMAND=0\n'
    sleep 0.2
    printf 'helper=$(pgrep -P $$ -x anduinos-quietd || :); [[ -z $helper ]] && printf STOPPED >%s/runtime-helper-stopped\n' "$TEST_ROOT"
    printf 'sudo docker p'
    sleep 0.2
    printf '\033[C\nexit\n'
}

runtime_reenable_input() {
    sleep 0.5
    printf 'export ANDUINOS_GUESS_COMMAND=0\n'
    sleep 0.2
    printf 'export ANDUINOS_GUESS_COMMAND=1\n'
    sleep 0.3
    printf 'sudo git che'
    sleep 0.2
    printf '\033[C\nexit\n'
}

run_session loader_lifecycle_input "$TEST_ROOT/loader-lifecycle.typescript"
[[ $(grep -o '_anduinos_guess_prompt_observe' "$TEST_ROOT/prompt-command" | wc -l) == 1 ]] ||
    fail 're-sourcing the loader duplicated PROMPT_COMMAND'
[[ $(<"$TEST_ROOT/fd-hygiene") == CLOSED ]] ||
    fail 'the helper inherited an unrelated Bash file descriptor'

run_session helper_recovery_input "$TEST_ROOT/helper-recovery.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'git checkout' ]] ||
    fail 'the native frontend did not recover after its helper was killed'

run_session accept_workflow_input "$TEST_ROOT/workflow.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt upgrade' ]] ||
    fail 'Right Arrow did not accept the apt workflow suggestion'
LC_ALL=C grep -aFq $'\033[90m' "$TEST_ROOT/workflow.typescript" ||
    fail 'the suggestion was accepted but never rendered as ghost text'

run_session apt_skeleton_input "$TEST_ROOT/apt-skeleton.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt update' ]] ||
    fail 'apt command skeleton was silent'

run_session apt_package_input "$TEST_ROOT/apt-package.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt install btop' ]] ||
    fail 'APT package popularity snapshot did not predict btop'
LC_ALL=C grep -aFq $'\033[90m' "$TEST_ROOT/apt-package.typescript" ||
    fail 'the APT package suggestion was accepted but never rendered'

run_session enter_native_input "$TEST_ROOT/enter.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'apt up' ]] ||
    fail 'Enter accepted ghost text instead of executing the typed line'

run_session docker_input "$TEST_ROOT/docker.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'docker exec -it kind_bassi' ]] ||
    fail 'live Docker context was not suggested'

run_session docker_mind_reading_input "$TEST_ROOT/docker-mind-reading.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'docker exec -it kind_bassi' ]] ||
    fail 'Docker listing did not predict the full exec workflow'

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

run_session explicit_current_path_input "$TEST_ROOT/explicit-path.typescript"
LC_ALL=C tr -d '\r' <"$TEST_ROOT/explicit-path.typescript" | \
    grep -q 'EXPLICIT_PATH_PREFIX' ||
    fail 'an explicit ./ prefix prevented current-directory path completion'

run_session ls_option_path_input "$TEST_ROOT/ls-option-path.typescript"
[[ -s $TEST_ROOT/ls-option-output ]] ||
    fail 'ls options prevented its final argument from using path completion'

run_session native_tab_input "$TEST_ROOT/native-tab.typescript"
[[ $(<"$TEST_ROOT/native-tab.args") == native-tab-result ]] ||
    fail 'the loader changed native programmable Tab completion behavior'

find "$TEST_ROOT" -maxdepth 1 -type f -name 'context-*' -delete
run_session transition_input "$TEST_ROOT/transition.typescript"
[[ $(<"$TEST_ROOT/context-two") == CONTEXT_TWO ]] ||
    fail 'adjacent-command context did not outrank generic history'
transition_file="$TEST_ROOT/home/.local/state/anduinos-bash-guess-command/transitions-v1"
[[ -s $transition_file && $(stat -c %a "$transition_file") == 600 ]] ||
    fail 'the transition graph was not persisted privately'

run_session created_directory_input "$TEST_ROOT/artifact-directory.typescript"
[[ $(<"$TEST_ROOT/artifact-pwd") == "$TEST_ROOT/smart-directory" ]] ||
    fail 'a verified created directory did not become a cross-command fact'

run_session ssh_host_input "$TEST_ROOT/ssh-host.typescript"
[[ $(<"$TEST_ROOT/ssh.args") == production-api ]] ||
    fail 'the background SSH config snapshot did not complete a host alias'

run_session dd_input_path_input "$TEST_ROOT/dd-input.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'dd if=/dev/' ]] ||
    fail 'dd if=/ did not receive its structured /dev/ path suggestion'

run_session dd_empty_output_input "$TEST_ROOT/dd-empty-output.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'dd of=' ]] ||
    fail 'an empty dd of= invented an output destination'

run_session destructive_history_input "$TEST_ROOT/destructive-history.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'rm -rf /nonexistent-anduinos-ghost-test' ]] ||
    fail 'ordinary destructive command text remained blocked from history completion'

# Keep opt-out lifecycle probes last: they deliberately toggle the master switch
# and must not influence the persistent ranking exercised above.
run_session_with_rcfile disabled_prediction_probe_input \
    "$TEST_ROOT/late-disabled.typescript" "$TEST_ROOT/late-disabled-bashrc"
[[ $(<"$TEST_ROOT/helper-stopped") == STOPPED ]] ||
    fail 'a setting after bash-completion left the helper running'
[[ $(<"$TEST_ROOT/sudo.args") == 'docker p' ]] ||
    fail 'a setting after bash-completion did not disable suggestions'
if LC_ALL=C grep -aFq $'\033[90m' "$TEST_ROOT/late-disabled.typescript"; then
    fail 'a setting after bash-completion still rendered ghost text'
fi

run_session runtime_disable_input "$TEST_ROOT/runtime-disabled.typescript"
[[ $(<"$TEST_ROOT/runtime-helper-stopped") == STOPPED ]] ||
    fail 'runtime opt-out left the helper running'
[[ $(<"$TEST_ROOT/sudo.args") == 'docker p' ]] ||
    fail 'runtime opt-out left suggestions active'

run_session runtime_reenable_input "$TEST_ROOT/runtime-reenabled.typescript"
[[ $(<"$TEST_ROOT/sudo.args") == 'git checkout' ]] ||
    fail 'runtime opt-in did not restore suggestions'

printf 'Native Readline interaction checks passed.\n'
