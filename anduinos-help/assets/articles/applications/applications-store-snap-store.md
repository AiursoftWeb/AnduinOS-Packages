# Snap: default blocking and explicit opt-out

AnduinOS blocks Snap by default through the `anduinos-no-snapd` package.
It supplies a negative APT pin and conflicts with `snapd`. Thus a normal
`sudo apt install snapd` reports no installation candidate. Applications that
require Snap are also blocked; this does not mean Ubuntu has stopped shipping it.

Use native packages or [Flatpak](../Flathub/Flathub.md) unless you specifically
need Snap. Installing Snap is an explicit opt-out of this default policy.

## Required package versions

This procedure requires `anduinos-desktop` **2.0.2-3 or newer** on systems
with that metapackage, and the revised `anduinos-no-snapd` **2.0.2-2 or newer**
when installing the blocking policy again. Older desktop versions depend on
no-snapd; removing it could also remove the desktop metapackage.

```bash
dpkg-query -W -f='${Package} ${Version}\n' anduinos-desktop anduinos-no-snapd
apt-cache policy anduinos-desktop anduinos-no-snapd snapd
```

If the required versions are not yet available from your configured AnduinOS
repository, stop and wait for them. Do not remove the desktop metapackage,
force dependencies, or delete the pin just to bypass this check.

## 1. Explicitly remove the blocker, keeping the desktop

For a system with `anduinos-desktop` installed, preview the operation first:

```bash
apt-get --simulate install anduinos-desktop anduinos-no-snapd-
```

The trailing `-` requests removal of that one package; explicitly requesting
`anduinos-desktop` requires the solver to keep/install the desktop metapackage.
With the old hard dependency, APT must refuse instead of silently dropping it.

Proceed only if no unrelated packages or desktop components will be removed:

```bash
sudo apt-get install anduinos-desktop anduinos-no-snapd-
```

Do not add `-y`; inspect the final plan again. Do not run `autoremove`.

On a deliberately minimal server **without** `anduinos-desktop`, do not
install the desktop merely to follow this guide. Instead simulate
`apt-get --simulate remove anduinos-no-snapd`, verify that only the blocker
will be removed, and then run `sudo apt-get remove anduinos-no-snapd`.

## 2. Check the pin and install Snap without removing packages

The blocker owns `/etc/apt/preferences.d/no-snap.pref`; its packaged pin
should disappear when the package is removed. Check rather than blindly
deleting files:

```bash
apt-cache policy snapd
```

If there is still no candidate, inspect your remaining APT preferences.
A manually created or locally retained blocking file may need to be backed up
and disabled explicitly after confirming its contents and ownership. Do not
delete all preferences or disable signature verification.

```bash
sudo apt update
apt-get --simulate --no-remove install snapd
sudo apt-get --no-remove install snapd
```

`--no-remove` makes APT abort if any package needs removal. If it refuses,
investigate; do not drop this guard. Restart the system when convenient to
complete Snap setup. See [Snap's Ubuntu installation guidance](https://snapcraft.io/install/snapd/ubuntu).

## 3. Optional graphical Snap Store

The graphical store is not required on SSH-only systems. Desktop users can
install it after the daemon is working:

```bash
sudo snap install snap-store
sudo snap install snapd-desktop-integration
```

Restart the desktop session if its menu entry has not appeared.

## Returning to the default blocking policy

Back up any Snap application data to independent storage first. Inspect
`snap list`, stop the relevant services, and remove applications you no longer
need using Snap's own tools. Removing applications or the daemon can affect
their data; an automatic Snap snapshot is not a substitute for an independent
backup.

Preview `apt-get --simulate remove snapd` and review removals before using
`sudo apt-get remove snapd`. Once the daemon has been removed, install the
revised blocker with removal protection:

```bash
no_snap_candidate=$(LC_ALL=C apt-cache policy anduinos-no-snapd | awk '/Candidate:/ {print $2}')
if [ -n "$no_snap_candidate" ] && [ "$no_snap_candidate" != '(none)' ] &&
   dpkg --compare-versions "$no_snap_candidate" ge 2.0.2-2; then
    sudo apt-get --no-remove install "anduinos-no-snapd=$no_snap_candidate"
else
    echo 'The data-preserving blocker is not available; stop here.' >&2
fi
```

Do not substitute the old blocker if this version is unavailable. Starting
with 2.0.2-2, no-snapd only applies the package policy; it no longer recursively
deletes Snap directories or forcibly unmounts them. This does not guarantee
that Snap's own removal operations preserve every application's data.

Never use a blanket `rm -rf` on Snap's system directories or `~/snap` as a
routine way to switch package policies.
