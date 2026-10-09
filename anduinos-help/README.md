# anduinos-help

AnduinOS Help — a **native** GTK 4 + Libadwaita documentation browser
for AnduinOS. It periodically clones the official AnduinOS-Docs git
repository and indexes its Markdown into a local SQLite database, so
search, navigation, and article rendering all work fully offline
after the initial sync.

## What it does

Inspired by [Aiursoft.DocsViewer](https://github.com/aiursoftweb/docsviewer),
the Help Center runs a background job on startup that:

1. **Clones** (or pulls) the
   [AnduinOS-Docs](https://github.com/AiursoftWeb/AnduinOS-Docs) git
   repository into `XDG_DATA_HOME/anduinos-help/docs-repo/`.
2. **Parses** `properdocs.yml` at the repo root to get the canonical
   sidebar navigation tree (the same file DocsViewer uses).
3. **Walks** the `Docs/` directory and indexes each `.md` file into a
   SQLite database (`XDG_DATA_HOME/anduinos-help/docs.db`) — one row
   per document with title, category, content, last-modified, source
   URLs.
4. **Persists** the nav tree in the same database so the sidebar can
   render the proper order/grouping without re-parsing YAML.

After the sync, everything works offline:

* **Native GTK article renderer** — Markdown is parsed into an IR and
  rendered with real GTK widgets (headings, paragraphs, code blocks
  with copy buttons, callouts, tables, lists, blockquotes). No
  webview.
* **FTS5 full-text search** — SQLite's built-in FTS5 virtual table
  powers search across title + content, with BM25 ranking and
  snippets.
* **Native sidebar** — built from the `properdocs.yml` nav tree, with
  the same hierarchy (Home → Release Notes → Install → Skills →
  Applications → Apkg → Servicing → Virtualization) you see on
  https://docs.anduinos.com/.
* **Native category + search pages** — Adwaita `ListBox` rows with
  article counts, subcategory grouping, and instant search-as-you-type.
* **Back / Forward / Home / Refresh** — full navigation history with
  keyboard shortcuts.

## Why this design

* **Native feel.** Real GTK widgets, not a webview wrapper. The
  article view uses the same Libadwaita typography, code-block styling,
  and callout colors as the rest of the desktop.
* **Always up to date.** The background sync runs on startup (if it's
  been > 6 hours since the last sync) and on demand via
  `Ctrl+R` / `Refresh Docs`. There's no bundled snapshot to drift out
  of date.
* **Offline-capable.** Once synced, the app needs no network
  connection — search and article rendering read from the local
  SQLite database.
* **Single source of truth.** The official AnduinOS-Docs git
  repository is the only copy of the documentation. The app caches it
  locally; it doesn't fork or republish.

## Install layout

The `.aosproj` installs:

* Python package:        `/usr/lib/python3/dist-packages/anduinos_help/`
* Launcher:               `/usr/bin/anduinos-help`
* Stylesheet:             `/usr/share/anduinos-help/style.css`
* Desktop entry:          `/usr/share/applications/com.anduinos.Help.desktop`
* App icon:               `/usr/share/icons/hicolor/scalable/apps/com.anduinos.Help.svg`
* Translations:           `/usr/share/locale/<lang>/LC_MESSAGES/anduinos-help.mo`

Runtime data (created on first launch):

* SQLite database:        `~/.local/share/anduinos-help/docs.db`
* Cloned docs repo:       `~/.local/share/anduinos-help/docs-repo/repo/`
* Last-sync metadata:     `~/.local/state/anduinos-help/last-sync.json`
* Rotating log:           `~/.local/state/anduinos-help/help.log`

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

The test suite covers:

* `test_store` — SQLite document store (upsert, lookup, FTS5 search,
  soft-delete, nav tree).
* `test_parser` — Markdown → IR block parser (headings, code,
  callouts, tables).
* `test_links` — URL allow-list security boundary.
* `test_version` — AnduinOS / base-distro detection.
* `test_diagnostics` — sensitive-data boundary for bug reports.

## Keyboard shortcuts

| Action                       | Shortcut          |
|------------------------------|-------------------|
| Go to home page              | `Ctrl+H`          |
| Refresh docs from upstream   | `Ctrl+R` or `F5`  |
| Focus search                 | `Ctrl+F` or `Ctrl+K` |
| System diagnostics           | `Ctrl+Shift+D`    |
| Report a problem             | `Ctrl+Shift+B`    |
| Open glossary                | `Ctrl+G`          |
| Toggle fullscreen            | `F11`             |
| Show keyboard shortcuts      | `Ctrl+?`          |
| Quit                         | `Ctrl+Q`          |

## Architecture

```
src/anduinos_help/
├── __init__.py
├── i18n.py                 # gettext initialisation
├── main.py                 # Adw.Application — kicks off background sync on startup
├── utils/
│   ├── __init__.py
│   ├── logging.py          # rotating file + stderr logging
│   ├── paths.py            # XDG dirs + SQLite + repo-clone paths
│   └── links.py            # URL allow-list + open_external()
├── system/
│   ├── __init__.py
│   ├── version.py          # AnduinOS / base-distro detection
│   └── diagnostics.py      # safe system-info collection for bug reports
├── docs/
│   ├── __init__.py
│   ├── store.py            # ★ SQLite store (documents + nav_entries + FTS5)
│   ├── sync.py             # ★ Background job: git clone/pull + Markdown index
│   ├── nav.py              # ★ properdocs.yml parser → ordered nav tree
│   ├── parser.py           # Markdown → IR blocks (markdown-it-py)
│   ├── loader.py           # Article/Catalog dataclasses (SQLite-backed)
│   └── search.py           # FTS5 search + command-extraction search
└── ui/
    ├── __init__.py
    ├── window.py           # Adw.ApplicationWindow + sidebar + content stack
    ├── sidebar.py          # category list (rebuilt from SQLite after sync)
    ├── home.py             # hero + search + popular topics + resources
    ├── article_view.py     # native GTK Markdown renderer
    ├── category.py         # native category listing page
    ├── search.py           # native search results page
    ├── code_block.py       # code block widget with copy button + language badge
    ├── dialogs.py          # about / diagnostics / refresh / report / glossary / shortcuts
    └── widgets.py          # small reusable GTK4 widgets
```

## License

* Application code: **GPL-3.0**
* AppStream metadata: **CC0-1.0**

The documentation itself is cloned at runtime from
<https://github.com/AiursoftWeb/AnduinOS-Docs> and licensed under
GPL-3.0 by that repository.
