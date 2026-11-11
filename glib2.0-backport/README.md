# Temporary Stonking GLib backport

Fixes the GLib network-monitor/proxy initialization deadlock that can prevent
GeoClue from starting. **Stonking only, amd64 and arm64. Resolute is unchanged.**

The two unmodified patches come from the GLib 2.90 stable branch:

- [Avoid asynchronous initialization deadlock](https://gitlab.gnome.org/GNOME/glib/-/commit/4301b49671da484da6c305ef1050700538b33e60).
- [Share synchronous and asynchronous reachability logic](https://gitlab.gnome.org/GNOME/glib/-/commit/32b9c00d9e43dd4a6dee5d53bf9b7418ef7de1fd).

See [GLib !5460](https://gitlab.gnome.org/GNOME/glib/-/merge_requests/5460) and
[Ubuntu #2173083](https://bugs.launchpad.net/bugs/2173083).
Network monitoring, libproxy and location permissions remain enabled.

## Build

Use a disposable native Ubuntu Stonking build environment with `deb` and
`deb-src` enabled for the signed Ubuntu release, updates and security archives.
Install `build-essential`, `dpkg-dev`, `devscripts`, `fakeroot` and `quilt`,
refresh the indexes, and install the original build dependencies:

```bash
sudo apt-get update --error-on=any
sudo apt-get build-dep glib2.0=2.90.0-1
bash build.sh /path/to/new-build-directory
```

The script downloads the original source through APT, applies the quilt series
and builds the complete Ubuntu package with `dpkg-buildpackage -us -uc`.
Its output includes the source package, all original binary packages and debug
symbols, plus build logs and package checksums in `evidence/`. Original Debian
maintainer scripts, dependencies, symbols and copyright are retained. Do not
disable upstream tests. Build separately on each native architecture.

This directory deliberately has no `.aosproj`: one source build produces
multiple interdependent Debian packages. Do not combine them into a renamed
GLib package or overwrite library files from another package's post-install
script. The build script never installs dependencies, changes APT sources,
uploads packages or changes the existing package CI pipeline.

## Adoption and retirement

The temporary revision `2.90.0-1aos1` sorts after the original Ubuntu revision
and before a normal Ubuntu security/update revision. The build stops if the
enabled source indexes contain a newer Ubuntu GLib revision; check its patches
before deciding whether another backport is needed.

Distribution must retain the original binary package names and exact-version
dependencies and target **only `stonking-addon`**, through the existing APKG
channel and human review. Publish the complete binary set, retaining source
artifacts. Existing Stonking installations must receive it through normal APT
upgrades; a new ISO alone is insufficient. Do not add a dependency or package
to Resolute, and do not enable Proposed for this fix.

Before distribution, validate normal APT candidate selection, upgrade and cold
boot on installed Stonking systems, GeoClue restart and client requests,
network/proxy behavior, and return to official packages. This source/build MR
does not add an automatic publication job.

Once official Ubuntu packages contain the fix and pass the same checks on both
architectures, withdraw **all** temporary GLib binaries from the AnduinOS APT
indexes and verify normal APT upgrades to Ubuntu's packages. AnduinOS's existing
repository priority can prefer its packages even over a newer Ubuntu version;
version ordering alone is not an exit mechanism. Remove this temporary build
directory after that transition. Do not add an epoch or permanent pin.
