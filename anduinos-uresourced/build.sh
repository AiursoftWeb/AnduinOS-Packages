#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/build-guards.sh
for tool in curl tar gcc pkg-config glib-compile-resources python3; do
    need_cmd "$tool"
done
: "${APKG_INPUTS_DIR:?Build with apkg}"
: "${APKG_STAGE_DIR:?Build with apkg}"

arch=${1:?Target architecture required}
host_arch=$(dpkg --print-architecture)
case "$arch" in
    amd64) triplet=x86_64-linux-gnu; cpu=x86_64 ;;
    arm64) triplet=aarch64-linux-gnu; cpu=aarch64 ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
esac
compiler=gcc
if [[ "$arch" != "$host_arch" ]]; then
    compiler=$triplet-gcc
fi
need_cmd "$compiler"

inputs=$APKG_INPUTS_DIR/extracted
work=$(mktemp -d "$PWD/obj/build.XXXXXX")
trap 'rm -rf "$work"' EXIT
curl --fail --location --silent --show-error --retry 3 \
    https://gitlab.freedesktop.org/benzea/uresourced/-/archive/v0.5.5/uresourced-v0.5.5.tar.bz2 \
    --output "$work/source.tar.bz2"
tar -xjf "$work/source.tar.bz2" -C "$work"
src=$work/uresourced-v0.5.5
sysroot=$work/sysroot
mkdir -p "$sysroot"
for package in systemd-dev libsystemd-dev libsystemd0 libpipewire-0.3-dev libpipewire-0.3-0t64 libspa-0.2-dev; do
    cp -a "$inputs/$package/." "$sysroot/"
done
# Relocate only fetched development metadata; leave the runner's GLib paths
# untouched. pkg-config's automatic prefix relocation misidentifies multiarch
# /usr/lib/<triplet>/pkgconfig as a prefix ending in /usr/lib.
python3 - "$sysroot" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for pc in root.rglob('*.pc'):
    pc.write_text(pc.read_text().replace('=/usr', '=' + str(root / 'usr')))
PY

export PYTHONPATH="$inputs/meson/usr/lib/python3/dist-packages:$inputs/packaging/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$inputs/ninja-$host_arch/usr/bin:$PATH"
export PKG_CONFIG_PATH="$sysroot/usr/lib/$triplet/pkgconfig:$sysroot/usr/share/pkgconfig"
export PKG_CONFIG_LIBDIR="/usr/lib/$triplet/pkgconfig:/usr/share/pkgconfig"
# The architecture-specific search path excludes host libraries in cross builds.
cat > "$work/machine.ini" <<EOF
[binaries]
c = '$compiler'
pkg-config = 'pkg-config'
[host_machine]
system = 'linux'
cpu_family = '$cpu'
cpu = '$cpu'
endian = 'little'
EOF
meson=(python3 "$inputs/meson/usr/bin/meson")
"${meson[@]}" setup "$work/build" "$src" --cross-file "$work/machine.ini" \
    --prefix=/usr --sysconfdir=/etc --libexecdir=libexec \
    --buildtype=release --wrap-mode=nodownload \
    -Dsystemdsystemunitdir=/usr/lib/systemd/system \
    -Dsystemduserunitdir=/usr/lib/systemd/user \
    -Dappmanagement=true -Dcgroupify=false
"${meson[@]}" compile -C "$work/build"
DESTDIR="$APKG_STAGE_DIR" "${meson[@]}" install -C "$work/build" --no-rebuild

# Keep the upstream user@.service activation even for offline/chroot installs,
# where no service manager is running to execute the package's postinst.
mkdir -p "$APKG_STAGE_DIR/usr/lib/systemd/system/user@.service.wants"
ln -s ../uresourced.service "$APKG_STAGE_DIR/usr/lib/systemd/system/user@.service.wants/uresourced.service"
doc=$APKG_STAGE_DIR/usr/share/doc/anduinos-uresourced
mkdir -p "$doc"
cp "$src/COPYING" "$doc/copyright"
cp "$src/README" "$src/NEWS.md" "$doc/"
cp "$APKG_INPUTS_DIR/manifest.json" "$doc/build-inputs.json"
# Explicitly declare the service for APKG's existing enable/restart/remove
# lifecycle without copying a second unit under /lib on merged-/usr systems.
mkdir -p deploy/usr/lib/systemd/system deploy/etc
cp "$APKG_STAGE_DIR/usr/lib/systemd/system/uresourced.service" deploy/usr/lib/systemd/system/
cp "$APKG_STAGE_DIR/etc/uresourced.conf" deploy/etc/
