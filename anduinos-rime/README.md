# AnduinOS Rime

This package provides the Rime Ice schema, dictionaries and Lua extensions
used for Chinese input on AnduinOS. It depends on `ibus-rime` and
`librime-plugin-lua`, but deliberately does not depend on
`language-selector-common`. The Lua plugin is required: the schema deliberately
generates pinyin comments for its correction filter, which removes them from
ordinary candidates and retains only useful pronunciation or spelling hints.

This directory is the canonical source of AnduinOS Rime. Upstream Rime Ice
updates are reviewed and imported directly into `assets/`; the package must not
download input method content from a second AnduinOS-Rime repository at build
time. Keeping the content and its Debian integration together makes every
package revision self-contained and reproducible from this repository alone.

The package owns its schema resources and three layered distribution files
below Rime's shared data directory:

```text
/usr/share/rime-data/anduinos_defaults.yaml
/usr/share/rime-data/default.custom.yaml
/usr/share/rime-data/rime_ice.custom.yaml
```

`anduinos_defaults.yaml` is the single source for the complete AnduinOS
defaults. The global entry point selects Rime Ice and configures global menu
behavior. The schema entry point keeps Rime Ice's input behavior, punctuation
and key bindings independent from unrelated global user preferences.

It does not own or replace either Ubuntu file:

```text
/usr/share/rime-data/default.yaml
/usr/share/language-selector/data/pkg_depends
```

Rime resolves configuration from the user's data directory first and the
shared data directory second. Accounts without a personal
`default.custom.yaml` therefore inherit the AnduinOS patch, while a user can
take control of global preferences with
`~/.config/ibus/rime/default.custom.yaml`, or customize the scheme itself with
`~/.config/ibus/rime/rime_ice.custom.yaml`. Package updates can update the
shared defaults without rewriting user homes.

The native AnduinOS installer reads its regional policy from
`anduinos-installer-beta/data/languages.json`. For a Simplified Chinese target
it verifies the Rime packages and shared configuration, then configures GNOME
to offer the physical XKB layout followed by the Rime IBus engine. It never
copies Rime configuration into `/etc/skel` or an existing user's home.

Version `2.0.1-2` contains an idempotent post-install migration that removes
the two diversions created by older releases and restores the Ubuntu-owned
files. It never adds a diversion. Once supported systems have passed through
this migration release, the post-install script itself can be removed.

Starting with `2.0.1-3`, the distribution patch is loaded directly from Rime's
shared data directory instead of being copied to new accounts by the installer.
