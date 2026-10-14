# Build against Resolute's libc, using the already qualified Ubuntu source.
FROM ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc-multilib debhelper patchutils python3 python3-apt \
    flex bison gawk po-debconf help2man texinfo xfonts-unifont libfreetype6-dev \
    gettext libdevmapper-dev libsdl2-dev xorriso cpio parted libfuse3-dev \
    fonts-dejavu-core liblzma-dev liblzo2-dev lzop dosfstools squashfs-tools \
    wamerican pkgconf bash-completion libefiboot-dev libefivar-dev \
    autoconf-archive ca-certificates curl quilt
WORKDIR /build
COPY sources.sha256 /build/sources.sha256
RUN for name in grub2_2.14.orig.tar.xz grub2_2.14-2ubuntu3.debian.tar.xz; do \
      curl --fail --location --retry 3 --connect-timeout 30 \
        "https://archive.ubuntu.com/ubuntu/pool/main/g/grub2/$name" -o "$name"; \
    done && sha256sum --check sources.sha256 && \
    mkdir source && tar -xf grub2_2.14.orig.tar.xz -C source --strip-components=1 && \
    tar -xf grub2_2.14-2ubuntu3.debian.tar.xz -C source
WORKDIR /build/source
RUN QUILT_PATCHES=debian/patches quilt push -a
ENV DEB_BUILD_OPTIONS="parallel=4 nocheck"
RUN debian/rules override_dh_autoreconf && \
    debian/rules build/grub-pc build/grub-efi-amd64 && \
    debian/rules install/grub-pc install/grub-efi-amd64 install/grub2-common && \
    dh_install -pgrub2-common --sourcedir=debian/tmp-grub-pc && \
    dh_install -pgrub-pc-bin --sourcedir=debian/tmp-grub-pc && \
    dh_install -pgrub-efi-amd64-bin --sourcedir=debian/tmp-grub-efi-amd64 && \
    mkdir /payload && \
    cp -a debian/grub2-common/. debian/grub-pc-bin/. debian/grub-efi-amd64-bin/. /payload/ && \
    install -Dm644 COPYING /payload/usr/share/doc/anduinos-grub/copyright
RUN dh_link -pgrub2-common && \
    dh_fixperms -pgrub2-common -pgrub-pc-bin -pgrub-efi-amd64-bin && \
    cp -a debian/grub2-common/. debian/grub-pc-bin/. debian/grub-efi-amd64-bin/. /payload/ && \
    chmod 0755 /payload/usr/lib/grub/grub-sort-version /payload/etc/kernel/postinst.d/zz-update-grub /payload/etc/kernel/postrm.d/zz-update-grub && \
    install -Dm644 debian/grub2-common.service /payload/usr/lib/systemd/system/grub2-common.service && \
    sed -i -e 's/@DEFAULT_TIMEOUT@/10/g' -e 's/@DEFAULT_CMDLINE@/quiet splash/g' \
      /payload/usr/share/grub/default/grub
COPY sbat.csv /build/sbat.csv
COPY bootstrap.cfg /build/bootstrap.cfg
# Preserve Ubuntu's baseline module list; add the Argon2id dependency closure.
RUN python3 -c 'from pathlib import Path; p=Path("debian/build-efi-images"); s=p.read_text(); s=s.replace("GRUB_MODULES=\"$CD_MODULES", "GRUB_MODULES=\"$CD_MODULES\n\tluks2\n\targon2", 1); s=s.replace("-p \"/EFI/$efi_vendor\"", "-p \"/EFI/$efi_vendor\" -c /build/bootstrap.cfg", 1); Path("/build/build-efi-images").write_text(s)' && \
    sh /build/build-efi-images obj/grub-pc/grub-mkimage \
      obj/grub-efi-amd64/grub-core /build/efi amd64 x86_64-efi x64 \
      /build/sbat.csv AnduinOS && \
    install -Dm644 /build/efi/grubx64.efi /payload/usr/lib/anduinos-grub/grubx64.efi && \
    install -Dm644 /build/sbat.csv /payload/usr/share/doc/anduinos-grub/sbat.csv && \
    install -Dm644 /build/sources.sha256 /payload/usr/share/doc/anduinos-grub/sources.sha256 && \
    dpkg-query -W > /payload/usr/share/doc/anduinos-grub/build-packages.txt && \
    obj/grub-pc/grub-install --version > /payload/usr/share/doc/anduinos-grub/source-version.txt
FROM scratch AS artifacts
COPY --from=build /payload/ /
