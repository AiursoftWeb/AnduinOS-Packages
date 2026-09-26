# anduinos-help

AnduinOS Help — official offline-readable documentation browser for AnduinOS,
built with Python + GTK 4 + Libadwaita.

## What it does

* Bundles **217 official AnduinOS documentation articles** sourced from
  [docs.anduinos.com](https://docs.anduinos.com/) and the
  [AnduinOS-Docs](https://github.com/AiursoftWeb/AnduinOS-Docs) repository
  (GPL-3.0). Articles retain their original source attribution.
* **Full-text search** with fuzzy matching across titles, headings, body
  text, tags, and code blocks. Hyphens are normalised so `wifi` matches
  `Wi-Fi`.
* **Command search** — type `apt` or `free` to find the article that
  documents that command.
* **Version-aware** — articles are tagged for AnduinOS 2.x; legacy
  articles are visually marked.
* **System diagnostics** with a "Copy diagnostic information" workflow
  for bug reports. No passwords, tokens, or private files are exposed.
* **Report a Problem** action that pre-fills a bug-report template with
  system information and links to the official issue tracker.
* **Offline-first** — all documentation is bundled; the update mechanism
  is opt-in via the menu and uses atomic staging with rollback.
* **Native GTK 4 + Libadwaita** UI that respects the desktop's light/dark
  theme and accent colour.

## Install layout

The `.aosproj` installs:

* Python package:        `/usr/lib/python3/dist-packages/anduinos_help/`
* Launcher:               `/usr/bin/anduinos-help`
* Bundled documentation:  `/usr/share/anduinos-help/`
* Desktop entry:          `/usr/share/applications/com.anduinos.Help.desktop`
* App icon:               `/usr/share/icons/hicolor/scalable/apps/com.anduinos.Help.svg`
* Translations:           `/usr/share/locale/<lang>/LC_MESSAGES/anduinos-help.mo`

## Build

```bash
apkg lint
apkg test --profile anduinos-package-release-test
apkg build --all
```

Inspect the resulting `.deb`:

```bash
dpkg-deb --info deploy/*.deb
dpkg-deb --contents deploy/*.deb
```

## Test

```bash
cd anduinos-help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \
    python3 -m unittest discover -s tests -v
```

## Adding a translation

1. Copy `po/anduinos-help.pot` to `po/<locale>.po` (e.g. `po/fr_FR.po`).
2. Translate the `msgstr ""` lines.
3. Run `bash compile-locales.sh` to compile `.mo` files for local testing.
4. The CI pipeline will compile locales via the `<PrebuildCommand>` in
   `.aosproj` before packaging.

## Documentation ingestion

The `scripts/sync_docs.py` tool is for **maintainer use only** — it is
not installed by the package. It re-syncs the bundled documentation from
the official AnduinOS docs website and GitHub repository:

```bash
python3 scripts/sync_docs.py
```

This fetches the current state of `https://docs.anduinos.com/`, downloads
the corresponding raw Markdown from
`raw.githubusercontent.com/AiursoftWeb/AnduinOS-Docs/master/`, normalises
the content, and regenerates `assets/articles/`, `assets/index.json`,
`assets/meta.json`, and `assets/sync-report.json`. Commit the result.

## License

* Application code: **GPL-3.0**
* Bundled documentation: **GPL-3.0** (from AnduinOS-Docs)
* AppStream metadata: **CC0-1.0**

Each bundled article retains its source URL and GitHub blob URL in
`assets/index.json` so the documentation remains auditable.
