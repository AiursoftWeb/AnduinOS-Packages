# Temporary UDisks2 provider synchronization fix

Fixes a provider startup race in Ubuntu UDisks2 on **Resolute and Stonking,
amd64 and arm64**. The uevent monitor can access `sysfs_to_block` before
`provider_start()` creates it, and its subsequent lookups lack the lock used
by device updates.

The patch initializes that table in the provider's instance initializer and
moves the lookup into the existing probing thread under `provider_lock`.
The monitor continues to queue incoming events without waiting for device
processing. No additional threads, locks, dependencies or startup delays are
introduced. Device monitoring remains enabled.

The race was exposed when [upstream moved udev monitoring into a dedicated
thread](https://github.com/storaged-project/udisks/commit/40438161e81b78eb70e9c5d2533e431a1e79b02b).
[GLib requires synchronization for shared hash-table access](https://docs.gtk.org/glib/threads.html).

## Build

Use a disposable native Ubuntu build environment matching the target suite,
with `deb` and `deb-src` enabled for the signed release, updates and security
archives. Install `build-essential`, `dpkg-dev`, `devscripts`, `fakeroot` and
`quilt`, refresh the indexes, and install the matching build dependencies:

```bash
sudo apt-get update --error-on=any
# Resolute:
sudo apt-get build-dep udisks2=2.10.91-1ubuntu2.1
# Stonking instead:
# sudo apt-get build-dep udisks2=2.11.2-1ubuntu1
bash build.sh /path/to/new-build-directory
```

The script downloads the original source through APT, preserves its complete
Debian patch series (including Resolute's security fixes), appends this quilt
patch and builds with `dpkg-buildpackage -us -uc`. It retains the original
binary names, exact-version dependencies, maintainer scripts, symbols,
documentation and copyright. Do not disable build checks or combine the
output into a renamed daemon package.

Like the existing GLib backport, this directory has no `.aosproj` or new CI
publication mechanism. The script does not install dependencies, modify APT
sources, upload packages or change ISO/meta-package inputs. Source artifacts,
debug symbols, build logs and binary checksums are retained with the output.

## Adoption and retirement

The temporary revisions are `2.10.91-1ubuntu2.1aos1` for Resolute and
`2.11.2-1ubuntu1aos1` for Stonking. They sort after their original Ubuntu
revisions and below subsequent Ubuntu update/security revisions. The build
stops if the enabled source indexes offer a newer revision: review the new
Ubuntu source and its fixes before rebuilding.

Adoption must preserve the complete original binary set, targeting only the
matching `resolute-addon` or `stonking-addon` repository through the existing
APKG channel and human review. Validate normal APT upgrades for existing
users, not just direct DEB installation or new ISO builds. This source/build
change does not itself publish those binaries.

Before adoption, verify an old/fixed deterministic startup comparison,
ordinary startup and service restarts, device event bursts without missing
devices, filesystem mount/unmount and temporary LUKS unlock/lock on both
architectures and suites. Diagnostic debugger delays and fatal-critical
settings belong only in disposable tests.

Once official Ubuntu packages include the fix and pass these checks, withdraw
all corresponding temporary binaries from the AnduinOS indexes and verify
normal APT selection of the official packages. Existing repository priority
can prefer an older AnduinOS package despite version ordering. Remove this
directory after that verified transition; do not add an epoch or permanent
pin.
