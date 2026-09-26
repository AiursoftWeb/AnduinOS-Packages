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

The background contains pre-rendered text and a menu frame. GRUB stretches the
whole canvas to the actual firmware graphics mode so the painted frame and
percentage-positioned selection remain together, even when a monitor's native
mode is unavailable during boot. Non-16:9 firmware modes may alter the artwork's
proportions, but do not hide menu content.

Small, scoped GRUB generator snippets add icon classes to the stock advanced
submenu and UEFI firmware entry, whose upstream generators omit them. Existing
Linux/Windows classes retain priority, and the original GRUB menu option is
restored before later custom entries. The snippets do not modify boot commands,
kernel arguments, or the distribution-owned generator scripts.
