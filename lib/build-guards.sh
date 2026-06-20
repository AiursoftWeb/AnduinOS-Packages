# build-guards.sh — validate build-time dependencies before running download.sh
# Source this file, then call `need_cmd <binary> [package-name]`.
# Missing commands print a loud error to stderr and exit 1.
# Call this:  need_cmd msgunfmt gettext

need_cmd() {
    local bin="$1"
    local pkg="${2:-$bin}"
    if ! command -v "$bin" >/dev/null 2>&1; then
        printf '\n%s\n' "============================================================" >&2
        printf 'BUILD ERROR: missing required tool: %s\n' "$bin" >&2
        printf '  Install it with:  sudo apt install -y %s\n' "$pkg" >&2
        printf '%s\n' "============================================================" >&2
        exit 1
    fi
}
