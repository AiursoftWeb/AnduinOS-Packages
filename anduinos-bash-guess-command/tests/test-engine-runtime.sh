#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="${ANDUINOS_QUIETD:-$ROOT/engine/target/release/anduinos-quietd}"
[[ -x $ENGINE ]] || {
    printf 'SKIP: build %s before runtime tests.\n' "$ENGINE"
    exit 0
}

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/home"
COUNT_FILE="$TEST_ROOT/docker.count"
ENTITY_COUNT_FILE="$TEST_ROOT/entity.count"
: >"$COUNT_FILE"
: >"$ENTITY_COUNT_FILE"

hex() {
    LC_ALL=C od -An -v -tx1 | tr -d ' \n'
}

now_ms() {
    local seconds=${EPOCHREALTIME%%.*}
    local fraction=${EPOCHREALTIME#*.}
    printf '%d' "$((10#$seconds * 1000 + 10#${fraction:0:3}))"
}

command_hex="$(printf %s 'sudo docker ps | grep mysql' | hex)"
query_hex="$(printf %s 'sudo docker exec -it ' | hex)"
cwd_hex="$(printf %s "$PWD" | hex)"
expected_hex="$(printf %s 'mysql-db' | hex)"
now_ms="$(now_ms)"

coproc QUIETD_PROCESS {
    PATH="$ROOT/tests/fixtures/runtime-bin:$PATH" \
    HOME="$TEST_ROOT/home" \
    ANDUINOS_GUESS_HISTORY=0 \
    ANDUINOS_RUNTIME_DOCKER_COUNT="$COUNT_FILE" \
    ANDUINOS_RUNTIME_ENTITY_COUNT="$ENTITY_COUNT_FILE" \
        "$ENGINE"
}
engine_out=${QUIETD_PROCESS[0]}
engine_in=${QUIETD_PROCESS[1]}

printf 'P\n' >&"$engine_in"
IFS= read -r -u "$engine_out" response || fail 'daemon closed during ping'
[[ $response == P ]] || fail 'daemon ping protocol failed'

printf 'O\t0\t%s\t%s\t%s\n' "$now_ms" "$command_hex" "$cwd_hex" >&"$engine_in"
IFS= read -r -u "$engine_out" response || fail 'daemon closed during observation'
[[ $response == A ]] || fail 'daemon did not acknowledge observation'

response=N
for _ in {1..50}; do
    now_ms="$(now_ms)"
    printf 'Q\t%s\t%s\n' "$now_ms" "$query_hex" >&"$engine_in"
    IFS= read -r -u "$engine_out" response || fail 'daemon closed during query'
    [[ $response == S$'\t'* ]] && break
    sleep 0.01
done
IFS=$'\t' read -r kind insertion confidence source <<<"$response"
[[ $kind == S && $insertion == "$expected_hex" ]] ||
    fail "unexpected Docker suggestion: $response"
[[ $confidence -ge 900 ]] || fail 'unique filtered container confidence is too low'

expect_suggestion() {
    local line=$1 expected=$2 encoded expected_encoded attempt
    encoded="$(printf %s "$line" | hex)"
    expected_encoded="$(printf %s "$expected" | hex)"
    for attempt in {1..50}; do
        now_ms="$(now_ms)"
        printf 'Q\t%s\t%s\n' "$now_ms" "$encoded" >&"$engine_in"
        IFS= read -r -u "$engine_out" response || fail 'daemon closed during entity query'
        IFS=$'\t' read -r kind insertion confidence source <<<"$response"
        [[ $kind == S && $insertion == "$expected_encoded" ]] && return 0
        sleep 0.01
    done
    fail "unexpected entity suggestion for $line: $response"
}

expect_suggestion 'kill 42' '42'
expect_suggestion 'systemctl status dock' 'er.service'
expect_suggestion 'git switch fea' 'ture-log'

calls_before="$(wc -l <"$COUNT_FILE")"
entity_calls_before="$(wc -l <"$ENTITY_COUNT_FILE")"
latencies=()
for _ in {1..100}; do
    now_ms="$(now_ms)"
    query_started=$now_ms
    printf 'Q\t%s\t%s\n' "$now_ms" "$query_hex" >&"$engine_in"
    IFS= read -r -u "$engine_out" response || fail 'daemon closed during repeated query'
    query_finished="$(now_ms)"
    latencies+=("$((query_finished-query_started))")
done
calls_after="$(wc -l <"$COUNT_FILE")"
entity_calls_after="$(wc -l <"$ENTITY_COUNT_FILE")"
[[ $calls_after == "$calls_before" ]] ||
    fail 'foreground queries executed Docker or another refresh'
[[ $entity_calls_after == "$entity_calls_before" ]] ||
    fail 'foreground queries executed Git, systemctl, or ps'
mapfile -t sorted_latencies < <(printf '%s\n' "${latencies[@]}" | sort -n)
p95=${sorted_latencies[94]}
[[ $p95 -le 10 ]] || fail "foreground pipe round-trip p95 is ${p95}ms"

printf 'X\n' >&"$engine_in"
IFS= read -r -u "$engine_out" response || fail 'daemon closed before quit acknowledgement'
[[ $response == A ]] || fail 'daemon quit protocol failed'
wait "$QUIETD_PROCESS_PID"

printf 'Quiet engine runtime checks passed: pipe p95=%sms.\n' "$p95"
