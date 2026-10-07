# Unlock theme

The session package owns `unlock-theme@anduinos.com`. GNOME's normal User Themes
extension is unchanged and still runs only on the desktop. GNOME disables that
extension before enabling this unlock-only extension, and disables this extension
before restoring desktop extensions. This keeps one application stylesheet owner
per session mode, using GNOME's supported `session-modes` mechanism:
https://gjs.guide/extensions/topics/session-modes.html

Only the selected Fluent family is reused, using the same theme-directory precedence
as User Themes. Relative assets stay relative to the original stylesheet. Empty,
missing or non-Fluent themes, disabled User Themes, and high contrast leave GNOME's
native unlock theme in control. A failed stylesheet load is rolled back. A small
readability overlay is shared with the existing native fallback, not a duplicate
Fluent theme. Neither PAM/authentication nor GDM resources are modified.
Setting changes are coalesced into a GLib idle callback, avoiding nested reloads
inside GNOME's theme notifications. Disable disconnects signals, cancels pending
work and removes only the stylesheet/overlay owned by this extension.

Default accounts enable the extension through `anduinos-dconf-defaults` after
logging in again. Accounts with a customized `enabled-extensions` list keep their
choices; enable `unlock-theme@anduinos.com` with GNOME Extensions or
`gnome-extensions enable unlock-theme@anduinos.com` after relogin if desired.
No package script rewrites per-user extension lists or Ubuntu-owned files.

## Checks

`bash tests/check-unlock-theme.sh` covers selection, ownership handoff, rollback,
repeat teardown, metadata/default integration and high-contrast fallback.

Before release, test on GNOME 46 and 50 with the packaged files after relogin:

- Lock/unlock repeatedly with correct and incorrect passwords; no Shell restart.
- Fluent dark/light and accent variants, including a bright wallpaper; username,
  prompt, password entry and error text must remain readable and focus visible.
- Enable high contrast both before and during lock; Fluent and the readability
  overlay must be removed. Turning high contrast off restores Fluent.
- Missing/non-Fluent theme, User Themes disabled, all extensions disabled, and
  this extension disabled: native unlock remains usable.
- After unlock, desktop User Themes regains its stylesheet and the overlay is gone.
- Suspend/resume on hardware with working display resume, plus keyboard layout,
  accessibility controls and a normal reboot. A VM whose unmodified baseline fails
  display resume does not count as a passed post-suspend test.
