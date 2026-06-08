# anduinos-fonts

AnduinOS default fonts and fontconfig. Ships Cascadia Code, Noto Sans/Serif, Nerd Fonts Symbols, and Twitter Color Emoji. Uses fontconfig to handle language-specific CJK/Thai/Arabic selection and per-app emoji fallback.

## Architecture

```
/etc/fonts/local.conf   ← fontconfig rules (edit at assets/local.conf)
/usr/share/fonts/
  CascadiaCode/         ← monospace default
  Noto_Sans/            ← sans-serif default (Latin-first)
  Noto_Serif/           ← serif default (Latin-first)
  NerdFontsSymbolsOnly/ ← terminal icons
  TwitterColorEmoji-SVGinOT/ ← default emoji (SVG-in-OT, pretty)
```

Plus `fonts-noto-color-emoji` (bitmap emoji) from Ubuntu repos, installed as a hard dependency.

## Language-specific font routing

`local.conf` routes `sans-serif` / `serif` / `monospace` per `lang` attribute:

| Language group | `lang` test | sans-serif | serif | monospace |
|---|---|---|---|---|
| Simplified Chinese | `zh-CN` | Noto Sans CJK SC | Noto Serif CJK SC | Noto Sans Mono CJK SC |
| Traditional Chinese (TW) | `zh-TW` | Noto Sans CJK TC | Noto Serif CJK TC | Noto Sans Mono CJK TC |
| Traditional Chinese (HK) | `zh-HK` | Noto Sans CJK HK | Noto Serif CJK TC | Noto Sans Mono CJK HK |
| Japanese | `ja` | Noto Sans CJK JP | Noto Serif CJK JP | Noto Sans Mono CJK JP |
| Korean | `ko` | Noto Sans CJK KR | Noto Serif CJK KR | Noto Sans Mono CJK KR |
| Thai | `th` | Noto Sans Thai | Noto Serif Thai | Cascadia Code (default) |
| Arabic | `ar` | Noto Naskh Arabic | Noto Naskh Arabic | Cascadia Code (default) |
| Latin/Cyrillic | `en`, `de`, `fr`, `ru`, etc. | Noto Sans (default) | Noto Serif (default) | Cascadia Code (default) |

Default rules put Latin/Cyrillic fonts before CJK SC, so punctuation stays Latin-style unless the document is tagged as CJK.

## Emoji: dual-font strategy

Chrome dropped SVG-in-OpenType support. Twitter Color Emoji is SVG-in-OT — Chrome can't render it.

**Our solution**: ship both Twitter Color Emoji (pretty, SVG-in-OT) and Noto Color Emoji (compatible, CBDT/CBLC bitmap). Default to Twitter for most apps. For Chromium-based browsers, use `mode="assign"` to **replace Twitter in-place** in the font list — never `mode="prepend"` which would push an emoji font to position 1 and blow up line-height metrics across all web pages.

```
Default (Firefox, GNOME, terminal):
  Noto Sans → ... → Twitter Color Emoji → Noto Color Emoji → ...

Chromium-based (chrome, chromium, edge, etc.):
  Noto Sans → ... → Noto Color Emoji → Noto Color Emoji → ...
  (Twitter replaced in-place, metrics baseline preserved)
```

The `prgname` filter uses `contains` on `chrom` to catch all Blink-based browsers in one rule. Edge gets its own `contains "edge"` rule.

## Adding a new language

1. Add the language to `local.conf` with a `lang`-gated `binding="strong"` override for each font family
2. Ensure the required fonts are installed (add as a `Dependency` in `.aosproj` or verify they're in `fonts-noto-core` / `fonts-noto-cjk`)
3. Rebuild and verify with `LANG=xx_XX.UTF-8 fc-match sans-serif`

## Debugging fontconfig

```bash
# Which font is used for sans-serif?
fc-match sans-serif

# For a specific language:
LANG=ja_JP.UTF-8 fc-match sans-serif

# For a specific app (prgname):
FC_DEBUG=1 fc-match sans-serif 2>&1 | grep prgname

# Live debugging — what's in the pattern?
fc-match -v sans-serif | grep -E "family:|prgname|lang:"

# Check if a font exists:
fc-list | grep -i "font-name"

# After editing local.conf:
fc-cache -f
```
