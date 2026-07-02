# anduinos-dconf-defaults

## Why `assets/` instead of `dconf/`?

Despite the word "dconf" in the package name, the files here live under
`assets/`, not `dconf/`. Here is the distinction:

| Directory | Purpose | Example |
|-----------|---------|---------|
| `assets/` | **Static files packaged as-is** into the `.deb`. These are authored by hand and committed to git. | `.gschema.override`, `.conf`, images |
| `dconf/` | **Dconf database directory** that gets installed under `/etc/dconf/db/`. Used by GNOME extension packages to ship pre-compiled dconf profiles. | `dconf/anduinos.d/` |

This package ships hand-written configuration files that get installed to
various locations:

| File | Installed to |
|------|-------------|
| `99-anduinos-defaults.gschema.override` | `/usr/share/glib-2.0/schemas/` |
| `01-custom-keybindings.conf` | `/etc/dconf/db/anduinos.d/` |
| `02-ptyxis-terminal.conf` | `/etc/dconf/db/anduinos.d/` |
| `03-system-extensions.conf` | `/etc/dconf/db/anduinos.d/` |
| `greeter.dconf-defaults.ini` | `/etc/gdm3/greeter.dconf-defaults` |
| `anduinos_text_smaller.png` | `/usr/share/anduinos-dconf-defaults/` |

Even though some targets are under `/etc/dconf/db/`, the **source files**
themselves are just static configs — they follow the `assets/` convention.
The `dconf/` directory name is reserved for packages that ship a whole
dconf database sub-tree as their source layout.
