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

The 16:9 wallpaper contains static branding and keyboard hints, but not the
menu panel. GRUB proportionally crops only the decorative right-hand side on
16:10 firmware modes; it draws the slim blue translucent menu frame, selection and
timeout in screen coordinates. Thus text and logo in the artwork retain their
proportions without detaching the selection from its panel. This also works
when firmware exposes a lower-resolution mode than the physical display.
The signed GRUB continues to render menu entries with its trusted Unicode font.
The nine menu frame slices can be regenerated with
`python3 tools/render_menu_box.py` (Pillow is needed only for this design step).

Small, scoped GRUB generator snippets add icon classes to the stock advanced
submenu and UEFI firmware entry, whose upstream generators omit them. Existing
Linux/Windows classes retain priority, and the original GRUB menu option is
restored before later custom entries. The snippets do not modify boot commands,
kernel arguments, or the distribution-owned generator scripts.
