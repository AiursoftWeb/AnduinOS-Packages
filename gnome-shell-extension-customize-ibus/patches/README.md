# IBus restart requests

`ibus-restart-time` is a persistent request token written by the preferences
window (milliseconds since the Unix epoch), not a daemon state or a notification
date. The upstream binding can replay an empty/default or unchanged token during
settings synchronisation, restarting IBus and showing a notification at login.
Parsing the empty value as Unix seconds also produces a 1970 notification date.

These patches snapshot the existing token before binding, accept only new positive
safe-integer tokens, consume a request before side effects, use the current time
for notifications, and unbind the property on disable. They do not suppress real
manual restart requests or disable the extension.

- `restart-requests-gnome46.patch`: upstream v90 (GNOME 46/47).
- `restart-requests.patch`: upstream v93/v94 (GNOME 48–51).

`download.sh` still uses the shared dynamic resolver. Patches are applied to fresh
downloads with zero fuzz and fail the build on an incompatible upstream change;
do not ignore patch failures. Reevaluate/remove the patches when upstream fixes
the request handling. Release tests execute the downloaded constructor, setter,
and cleanup with real GObject/GSettings and an isolated in-memory backend; only
GNOME Shell UI and daemon effects are mocked.

Run `apkg test --profile anduinos-package-release-test` in this package directory.
The ISO's offline Live notification check must remain enabled. Passing these unit
tests does not replace repeated Chinese/English cold-boot acceptance testing.
