#!/usr/bin/env bash
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "1. Checking shell script syntax..."
bash -n "$PKG_DIR/build.sh"
bash -n "$PKG_DIR/compile-locales.sh"

echo "2. Compiling and running core C unit tests..."
# shellcheck disable=SC2046
gcc -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags gio-2.0 glib-2.0) \
    "$PKG_DIR/src/copy_file_path_core.c" \
    "$PKG_DIR/tests/test_copy_file_path_core.c" \
    -o "$TMP_DIR/test_copy_file_path_core" \
    $(pkg-config --libs gio-2.0 glib-2.0)
"$TMP_DIR/test_copy_file_path_core" --verbose

echo "3. Compiling and verifying gettext catalogs..."
bash "$PKG_DIR/compile-locales.sh"
python3 - "$PKG_DIR/locale" <<'PY'
import gettext, sys
localedir = sys.argv[1]
zh = gettext.translation("anduinos-copy-file-path", localedir=localedir, languages=["zh_CN"])
assert zh.gettext("Copy Path") == "复制路径", zh.gettext("Copy Path")
assert zh.gettext("Copy Paths") == "复制路径", zh.gettext("Copy Paths")
fr = gettext.translation("anduinos-copy-file-path", localedir=localedir, languages=["fr"])
assert fr.gettext("Copy Path") != "Copy Path"
assert fr.gettext("Copy Paths") != "Copy Paths"
print("Locale smoke assertions passed (zh_CN, fr).")
PY

echo "4. Building native Nautilus extension (amd64) and checking exported symbols..."
bash "$PKG_DIR/build.sh" amd64
SO_PATH="$PKG_DIR/obj/amd64/libnautilus-copy-file-path.so"
test -s "$SO_PATH"

EXPORTS="$(nm -D --defined-only "$SO_PATH")"
for sym in nautilus_module_initialize nautilus_module_shutdown nautilus_module_list_types; do
    if ! grep -qw "$sym" <<<"$EXPORTS"; then
        echo "Missing required Nautilus module symbol: $sym" >&2
        exit 1
    fi
done

echo "All anduinos-copy-file-path tests passed."
