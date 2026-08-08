#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
font_dir="$repo_dir/assets/NerdFontsSymbolsOnly"
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/anduinos-fonts-test.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT HUP INT TERM

printf '%s  %s\n' \
  adc6d3e4a7cd6ac90eb5355d9a75f4f3078d9516eb1d35b1b4c640cac15264c8 \
  "$font_dir/SymbolsNerdFont-Regular.ttf" \
  14ece2cc91d5a4762ab3e7b76a19a489a1be9f0ec9e13dfadd81cd49fe6a1ad7 \
  "$font_dir/SymbolsNerdFontMono-Regular.ttf" | sha256sum --check --status

mkdir -p "$work_dir/cache"
cat >"$work_dir/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/usr/share/fonts</dir>
  <dir>$font_dir</dir>
  <cachedir>$work_dir/cache</cachedir>
  <include>$repo_dir/assets/local.conf</include>
</fontconfig>
EOF

for codepoint in f120 f308 f013 f07b f418 f0079 e0b0; do
  matches=$(FONTCONFIG_FILE="$work_dir/fonts.conf" \
    fc-list ":family=Symbols Nerd Font:charset=$codepoint" file)
  test -n "$matches" || {
    echo "Nerd Symbols does not cover U+$(printf '%s' "$codepoint" | tr '[:lower:]' '[:upper:]')" >&2
    exit 1
  }
done

glyphs='󰁹'
FONTCONFIG_FILE="$work_dir/fonts.conf" pango-view \
  --no-display \
  --font='monospace 24' \
  --text="$glyphs" \
  --output="$work_dir/nerd-symbols.png" \
  --serialize-to="$work_dir/nerd-symbols.layout"

test -s "$work_dir/nerd-symbols.png"
grep -q '"unknown-glyphs" : 0' "$work_dir/nerd-symbols.layout"
grep -q '"checksum" : "adc6d3e4a7cd6ac90eb5355d9a75f4f3078d9516eb1d35b1b4c640cac15264c8"' \
  "$work_dir/nerd-symbols.layout"
if grep -q 'Unifont' "$work_dir/nerd-symbols.layout"; then
  echo 'Pango selected Unifont instead of Symbols Nerd Font' >&2
  exit 1
fi

echo 'Nerd Fonts v3.0.2 hashes, glyph coverage, and Pango rendering: PASS'
