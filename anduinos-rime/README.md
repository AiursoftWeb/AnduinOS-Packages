# AnduinOS Rime

This package provides the Rime Ice schema, dictionaries and Lua extensions
used for Chinese input on AnduinOS. It depends on `ibus-rime`, but deliberately
does not depend on `language-selector-common`.

The package owns its schema resources below `/usr/share/rime-data/` and its
installer-facing customization template at:

```text
/usr/share/anduinos-rime/default.custom.yaml
```

It does not own or replace either Ubuntu file:

```text
/usr/share/rime-data/default.yaml
/usr/share/language-selector/data/pkg_depends
```

The native AnduinOS installer reads its regional policy from
`anduinos-installer-beta/data/languages.json`. For a Chinese target it verifies
the Rime packages and files, copies the customization template into the target
`/etc/skel/.config/ibus/rime/`, and configures GNOME to offer the physical XKB
layout followed by the Rime IBus engine.

Version `2.0.1-2` contains an idempotent post-install migration that removes
the two diversions created by older releases and restores the Ubuntu-owned
files. It never adds a diversion. Once supported systems have passed through
this migration release, the post-install script itself can be removed.
