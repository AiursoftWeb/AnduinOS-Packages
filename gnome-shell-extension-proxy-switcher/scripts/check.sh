#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTENSION="$ROOT/assets/proxy-switcher@anduinos.com"
SCHEMAS="$EXTENSION/schemas"
METADATA="$EXTENSION/metadata.json"

gjs -m "$ROOT/tests/proxy-state.test.js"
glib-compile-schemas --strict --dry-run "$SCHEMAS"

jq -e '
    .uuid == "proxy-switcher@anduinos.com" and
    .["gettext-domain"] == "proxy-switcher@anduinos.com" and
    .["settings-schema"] == "org.anduinos.shell.extensions.proxy-switcher" and
    (.["shell-version"] | index("46") != null) and
    (.["shell-version"] | index("50") != null) and
    (.version >= 29)
' "$METADATA" >/dev/null

test "$(find "$ROOT/po" -maxdepth 1 -type f -name '*.po' | wc -l)" -ge 100
test -f "$ROOT/po/zh_CN.po"
while IFS= read -r -d '' po_file; do
    msgfmt --check --check-format "$po_file" --output-file=/dev/null
done < <(find "$ROOT/po" -maxdepth 1 -type f -name '*.po' -print0 | sort -z)

grep -Fq 'this._indicator._addIndicator()' "$EXTENSION/extension.js"
grep -Fq "'checked'" "$EXTENSION/extension.js"
grep -Fq "'visible'" "$EXTENSION/extension.js"
grep -Fq 'GObject.BindingFlags.SYNC_CREATE' "$EXTENSION/extension.js"
grep -Fq 'org.anduinos.shell.extensions.proxy-switcher' \
    "$SCHEMAS/org.anduinos.shell.extensions.proxy-switcher.gschema.xml"
test "$(find "$SCHEMAS" -maxdepth 1 -type f -name '*.xml' | wc -l)" -eq 1

printf '%s  %s\n' \
    '8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643' \
    "$ROOT/COPYING" | sha256sum --check --status
grep -Fq '5b63ce78f81b79baf6eb9bea4ee12d2192ef966c' "$ROOT/UPSTREAM.md"

test ! -e "$ROOT/download.sh"
if rg -n 'resolve-gnome-ext|extensions\.gnome\.org/download-extension|curl|wget' \
    "$ROOT/build.sh" "$ROOT/gnome-shell-extension-proxy-switcher.aosproj"; then
    echo 'Build must not download extension source' >&2
    exit 1
fi

if rg -n 'ProxySwitcher@flannaghan\.com|com-flannaghan-ProxySwitcher|migrate-legacy' \
    "$ROOT/assets" "$ROOT/tests" \
    "$ROOT/gnome-shell-extension-proxy-switcher.aosproj"; then
    echo 'Legacy extension identity must not be shipped' >&2
    exit 1
fi

echo 'Proxy Switcher source and package checks passed'
