# anduinos-fonts

AnduinOS default fonts and fontconfig. Ships Cascadia Code, Noto Sans/Serif, Nerd Fonts Symbols, and Twemoji (COLRv1). Uses fontconfig to handle language-specific CJK/Thai/Arabic selection and emoji fallback.

## Architecture

```
/etc/fonts/local.conf   ← fontconfig rules (edit at assets/local.conf)
/usr/share/fonts/
  CascadiaCode/         ← monospace default
  Noto_Sans/            ← sans-serif default (Latin-first)
  Noto_Serif/           ← serif default (Latin-first)
  NerdFontsSymbolsOnly/ ← terminal icons
  Twemoji/              ← default emoji (COLRv1, vector)
```

Plus `fonts-noto-color-emoji` (CBDT/CBLC bitmap) from Ubuntu repos as fallback.

## Language-specific font routing

`local.conf` routes `sans-serif` / `serif` / `monospace` per `lang` attribute:

| Language group | `lang` test | sans-serif | serif | monospace |
|---|---|---|---|---|
| Simplified Chinese | `zh-CN` | Noto Sans CJK SC | Noto Serif CJK SC | Noto Sans Mono CJK SC |
| Traditional Chinese (TW) | `zh-TW` | Noto Sans CJK TC | Noto Serif CJK TC | Noto Sans Mono CJK TC |
| Traditional Chinese (HK) | `zh-HK` | Noto Sans CJK HK | Noto Serif CJK HK | Noto Sans Mono CJK HK |
| Japanese | `ja` | Noto Sans CJK JP | Noto Serif CJK JP | Noto Sans Mono CJK JP |
| Korean | `ko` | Noto Sans CJK KR | Noto Serif CJK KR | Noto Sans Mono CJK KR |
| Thai | `th` | Noto Sans Thai | Noto Serif Thai | Cascadia Code (default) |
| Arabic | `ar` | Noto Naskh Arabic | Noto Naskh Arabic | Cascadia Code (default) |
| Latin/Cyrillic | `en`, `de`, `fr`, `ru`, etc. | Noto Sans (default) | Noto Serif (default) | Cascadia Code (default) |

Default rules put Latin/Cyrillic fonts before CJK SC, so punctuation stays Latin-style unless the document is tagged as CJK.

Note: Simplified Chinese (`zh-CN`) and Latin scripts don't have explicit `<test name="lang">` blocks. They implicitly route to `Noto Sans CJK SC` and `Noto Sans` via the primary fallback queue in the global defaults, so no explicit lang match is needed.

## Emoji: COLRv1 with Noto fallback

FreeType 2.13 on Ubuntu lacks SVG-in-OT support (`FT_CONFIG_OPTION_SVG` not compiled), so SVG-format emoji fonts are unrenderable. We use **COLRv1** format (Twemoji) which FreeType, Harfbuzz, Chrome, and GTK all support natively.

```
All apps:
  Noto Sans → ... → Twemoji (COLRv1) → Noto Color Emoji (CBDT/CBLC bitmap) → ...
```

No per-app workarounds needed — COLRv1 works everywhere.

Font source: [TCOTC/twemoji-colr](https://github.com/TCOTC/twemoji-colr) (follows [jdecked/twemoji](https://github.com/jdecked/twemoji) releases).

## Licensing

This package is a collection of independently licensed works. AnduinOS
packaging and configuration files are GPL-3.0-only; the bundled fonts retain
their upstream licenses:

- Cascadia Code and Noto Sans/Serif: SIL Open Font License 1.1
- Nerd Fonts Symbols Only: composite work under MIT, OFL-1.1,
  CC-BY-4.0, Apache-2.0, Unlicense, and source-specific permissive terms
- Twemoji font software: MIT
- Twemoji artwork: Creative Commons Attribution 4.0 International

The authoritative copyright notices, upstream sources, attribution, and full
license texts are in [`COPYRIGHT`](COPYRIGHT). The binary package installs the
same information at `/usr/share/doc/anduinos-fonts/copyright` together with
the individual upstream license files.

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

# Which emoji font for a character?
fc-match -s ":charset=1F52B"

# After editing local.conf:
fc-cache -f
```
