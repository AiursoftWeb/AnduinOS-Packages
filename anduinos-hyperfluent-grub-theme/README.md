# AnduinOS HyperFluent GRUB Theme

Optional GRUB menu artwork for installed AnduinOS systems. The package adds
its own theme and `/etc/default/grub.d/30-anduinos-hyperfluent.cfg`, then uses
the distribution's `update-grub` to refresh the generated menu. Removing the
package and regenerating GRUB returns to the next surviving theme policy.

Other GRUB defaults, EFI binaries, initrds and recovery entries are untouched.
The installer carries this package from Live media into newly installed
systems; existing users can install or remove it independently.

The Live ISO copies the package's theme to `/boot/grub/themes/` so GRUB can
read it before the Live filesystem is mounted. The theme uses signed GRUB's
trusted Unicode font to remain compatible with Secure Boot.
