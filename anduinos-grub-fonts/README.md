# AnduinOS GRUB Fonts

This package installs the AnduinOS 28 px GNU Unifont PF2 font and the default
GRUB graphics-mode fallback list. It is intentionally separate from desktop
fonts and general system tuning: this package owns only the installed system's
GRUB font policy and its lifecycle.

## Installed files

- `/usr/share/grub/anduinos/anduinos-unicode-28.pf2`
- `/etc/default/grub.d/20-anduinos-font.cfg`

The package refreshes GRUB after installation, upgrade, removal, and purge on a
normal running system. In a chroot it defers the refresh; the AnduinOS installer
runs `update-grub` after deploying the target system.

## Reproducing the font

The packaged PF2 was generated from Ubuntu's `fonts-unifont` package using
`grub-mkfont --size=28`. Run `./generate-font.sh` on the recorded toolchain to
regenerate it and verify its SHA-256 checksum.

Recorded inputs:

- `fonts-unifont` `1:16.0.04-1build1`
- `grub2-common` `2.14-2ubuntu2.1`
- GNU Unifont `16.0.04`

The font is licensed under GPL-2.0-or-later. See `assets/copyright` for source
and copyright information.
