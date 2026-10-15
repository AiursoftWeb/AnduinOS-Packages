#!/bin/bash
set -euo pipefail
cd /build/source
parameters=$(python3 -I /build/architecture.py shell "$(dpkg --print-architecture)")
eval "$parameters"

efi_package="grub-efi-$BOOT_DEB_ARCH"
common_package=$efi_package
platforms=("$efi_package")
if [ -n "$BOOT_BIOS_PLATFORM" ]; then
    common_package=grub-pc
    platforms=(grub-pc "$efi_package")
fi

debian/rules override_dh_autoreconf
for package in "${platforms[@]}"; do
    debian/rules "build/$package" "install/$package"
done
debian/rules install/grub2-common
dh_install -pgrub2-common --sourcedir="debian/tmp-$common_package"
packages=(grub2-common)
for package in "${platforms[@]}"; do
    dh_install -p"$package-bin" --sourcedir="debian/tmp-$package"
    packages+=("$package-bin")
done
dh_link -pgrub2-common
mkdir -p /payload
for package in "${packages[@]}"; do
    dh_fixperms -p"$package"
    cp -a "debian/$package/." /payload/
done
chmod 0755 /payload/usr/lib/grub/grub-sort-version \
    /payload/etc/kernel/postinst.d/zz-update-grub /payload/etc/kernel/postrm.d/zz-update-grub
install -Dm644 debian/grub2-common.service /payload/usr/lib/systemd/system/grub2-common.service
sed -i -e 's/@DEFAULT_TIMEOUT@/10/g' -e 's/@DEFAULT_CMDLINE@/quiet splash/g' \
    /payload/usr/share/grub/default/grub

# Preserve Ubuntu's baseline modules and add the Argon2id dependency closure.
python3 - <<'PY'
from pathlib import Path
source = Path("debian/build-efi-images").read_text()
for old, new in (
    ('GRUB_MODULES="$CD_MODULES', 'GRUB_MODULES="$CD_MODULES\n\tluks2\n\targon2'),
    ('-p "/EFI/$efi_vendor"', '-p "/EFI/$efi_vendor" -c /build/bootstrap.cfg'),
):
    if source.count(old) != 1:
        raise SystemExit(f"Upstream EFI recipe changed: {old}")
    source = source.replace(old, new, 1)
Path("/build/build-efi-images").write_text(source)
PY
# Keep upstream component revocation generations, but identify our modified
# binary as AnduinOS rather than claiming Ubuntu's distribution signature.
version=$(dpkg-parsechangelog -SVersion)
upstream_version=${version%-*}
sed -e "s/@DEB_VERSION@/$version/g" -e "s/@UPSTREAM_VERSION@/$upstream_version/g" \
    -e '/^grub\.ubuntu[^,]*,/d' debian/sbat.ubuntu.csv.in > /build/sbat.csv
printf 'grub.anduinos,1,AnduinOS,anduinos-grub,%s,https://www.anduinos.com/\n' "$version" >> /build/sbat.csv
sh /build/build-efi-images "obj/$common_package/grub-mkimage" \
    "obj/$efi_package/grub-core" /build/efi "$BOOT_DEB_ARCH" \
    "$BOOT_GRUB_PLATFORM" "$BOOT_EFI_SUFFIX" /build/sbat.csv AnduinOS
install -Dm644 "/build/efi/grub$BOOT_EFI_SUFFIX.efi" "/payload/usr/lib/anduinos-grub/grub$BOOT_EFI_SUFFIX.efi"
install -Dm644 COPYING /payload/usr/share/doc/anduinos-grub/copyright
install -Dm644 /build/sbat.csv /payload/usr/share/doc/anduinos-grub/sbat.csv
install -Dm644 /build/download/sources.sha256 /payload/usr/share/doc/anduinos-grub/sources.sha256
dpkg-query -W > /payload/usr/share/doc/anduinos-grub/build-packages.txt
"obj/$common_package/grub-install" --version > /payload/usr/share/doc/anduinos-grub/source-version.txt
