#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH=${1:-amd64}
CARAPACE="$ROOT/deploy/$ARCH/carapace"
OUTPUT="$ROOT/engine/specs/generated-command-tree.tsv"

[[ -x $CARAPACE ]] || {
    printf 'update-command-specs.sh: missing %s; run download.sh %s first\n' "$CARAPACE" "$ARCH" >&2
    exit 1
}
command -v jq >/dev/null || {
    printf 'update-command-specs.sh: jq is required to generate the compact command tree\n' >&2
    exit 1
}

temporary_root="$(mktemp -d)"
list="$temporary_root/completers.json"
exports="$temporary_root/exports"
rows="$temporary_root/rows"
path_rows="$temporary_root/path-rows"
generated="$temporary_root/generated"
build_home="$temporary_root/home"
mkdir -p "$exports" "$build_home"
trap 'rm -rf -- "$temporary_root"' EXIT

# Only compiled, declarative completers are exported. Bridge completers inspect
# installed host programs, so including them would make the checked-in corpus
# depend on the developer's machine.
env -i HOME="$build_home" PATH=/nonexistent CARAPACE_BRIDGES='' \
    "$CARAPACE" --list >"$list"
jq -r '
    to_entries[]
    | select(any(.value[]; .package != null))
    | .key
    | select(test("^[[:alnum:]_][[:alnum:]_.+@-]*$") and (endswith("@") | not))
    | select(. != "sudo" and . != "command" and . != "time")
' "$list" >"$temporary_root/commands"

export CARAPACE exports build_home
_carapace_export_one() {
    command_name=$1
    timeout 8s env -i HOME="$build_home" PATH=/nonexistent CARAPACE_BRIDGES='' \
        "$CARAPACE" "$command_name" export >"$exports/$command_name.json" 2>/dev/null ||
        rm -f -- "$exports/$command_name.json"
}
export -f _carapace_export_one

# Exporting the static command trees is CPU-bound and hermetic. Keep the
# parallelism modest so this remains friendly on small package-builder VMs.
xargs -r -n1 -P4 bash -c '_carapace_export_one "$1"' _ \
    <"$temporary_root/commands"

# Each row is: complete command path <TAB> its immediate static actions <TAB>
# its local/persistent options.
# Aliases get equivalent paths, allowing e.g. `kubectl create deploy ...` and
# `kubectl create deployment ...` to traverse the same grammar. Positional
# completions are deliberately absent: those are resolved from live state by
# the tiny runtime daemon.
: >"$rows"
while IFS= read -r command_name; do
    export_file="$exports/$command_name.json"
    if [[ ! -s $export_file ]] || ! jq -e 'type == "object"' "$export_file" >/dev/null; then
        printf '%s\t-\n' "$command_name" >>"$rows"
        continue
    fi
    jq -r --arg root "$command_name" '
        def names:
            ([.Name] + (.Aliases // []))
            | map(select(
                type == "string"
                and test("^[[:alnum:]_][[:alnum:]_.+@-]*$")
                and (endswith("@") | not)
              ))
            | unique;
        def options:
            [(.LocalFlags // [])[], (.PersistentFlags // [])[]
              | (if (.Longhand // "") != "" then "--" + .Longhand else empty end),
                (if (.Shorthand // "") != "" then "-" + .Shorthand else empty end)]
            | map(select(test("^--?[[:alnum:]][[:alnum:]_-]*$")))
            | unique;
        def walk($paths):
            . as $node
            | (($node.Commands // []) | map(names[]) | unique) as $actions
            | ($node | options) as $options
            | $paths[] as $path
            | [$path,
               (if ($actions | length) > 0 then ($actions | join(",")) else "-" end),
               (if ($options | length) > 0 then ($options | join(",")) else "-" end)]
              | @tsv,
              (($node.Commands // [])[]
                | . as $child
                | (names) as $child_names
                | walk([
                    $paths[] as $parent
                    | $child_names[] as $name
                    | ($parent + " " + $name)
                  ]));
        walk([$root])
    ' "$export_file" >>"$rows"
done <"$temporary_root/commands"

# Probe positional completion in an isolated directory containing only two
# synthetic path entries. This records the *kind* of a slot without capturing
# any filename from the build host.
probe="$temporary_root/path-probe"
mkdir -p "$probe/anduinos_path_probe_dir"
touch "$probe/anduinos_path_probe_file"
export probe
_carapace_probe_path() {
    command_path=$1
    read -r -a words <<<"$command_path"
    root_command=${words[0]}
    raw="$(
        cd "$probe"
        timeout 1s env -i HOME="$build_home" PATH=/nonexistent CARAPACE_BRIDGES='' \
            "$CARAPACE" "$root_command" bash "${words[@]}" \
            anduinos_path_probe 2>/dev/null || true
    )"
    if [[ $raw == *anduinos_path_probe_* ]]; then
        printf '%s\n' "$command_path"
    fi
}
export -f _carapace_probe_path
cut -f1 "$rows" |
    xargs -r -d '\n' -n1 -P12 bash -c '_carapace_probe_path "$1"' _ |
    LC_ALL=C sort -u >"$temporary_root/path-commands"
awk -F '\t' 'BEGIN { OFS = "\t" }
    NR == FNR { path[$1] = 1; next }
    { print $1, $2, $3, (path[$1] ? "path" : "-") }
' "$temporary_root/path-commands" "$rows" >"$path_rows"

# Docker exposes many grouped commands through legacy root aliases, while
# Compose v2 nests the separately specified docker-compose grammar. Materialize
# those paths so the compact runtime understands the forms users actually type.
awk -F '\t' 'BEGIN { OFS = "\t" }
    $1 == "docker-compose" || $1 ~ /^docker-compose / {
        alias = $1
        sub(/^docker-compose/, "docker compose", alias)
        print alias, $2, $3, $4
    }
    $1 ~ /^docker (container|image) [^ ]+/ {
        alias = $1
        sub(/^docker (container|image) /, "docker ", alias)
        print alias, $2, $3, $4
    }
' "$path_rows" >"$temporary_root/aliases"

{
    printf '# Generated from Carapace 1.7.3 export trees; do not edit by hand.\n'
    awk -F '\t' '!seen[$1]++' "$path_rows" "$temporary_root/aliases" |
        LC_ALL=C sort -t $'\t' -k1,1
} >"$generated"

mv "$generated" "$OUTPUT"
rm -rf -- "$temporary_root"
trap - EXIT
printf 'Generated %s grammar nodes for %s commands in %s\n' \
    "$(($(wc -l <"$OUTPUT") - 1))" \
    "$(awk -F '\t' 'NR > 1 && $1 !~ / / { count++ } END { print count + 0 }' "$OUTPUT")" \
    "$OUTPUT"
