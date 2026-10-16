#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${APKG_INPUTS_DIR:?Build with apkg and BuildInput support}"
: "${APKG_STAGE_DIR:?Build with apkg}"
inputs=$APKG_INPUTS_DIR/extracted
input_field() {
    local files=("$APKG_INPUTS_DIR"/debs/"$1"/*.deb)
    test "${#files[@]}" = 1
    dpkg-deb -f "${files[0]}" "$2"
}

resolve_architectures() {
    arch=$(input_field modules Architecture)
    host_arch=$(dpkg --print-architecture)
    case "$arch" in
        amd64) platform=x86_64-efi; suffix=x64 ;;
        arm64) platform=arm64-efi; suffix=aa64 ;;
        *) echo "Unsupported target architecture: $arch" >&2; exit 1 ;;
    esac
    case "$host_arch" in
        amd64|arm64) ;;
        *) echo "Unsupported build host: $host_arch" >&2; exit 1 ;;
    esac
    tools=$inputs/tools-$arch
}

validate_input_versions() {
    local module_version tool_arch tool_version
    module_version=$(input_field modules Version)
    test "$module_version" = "$(input_field baseline Version)" || {
        echo 'Ubuntu EFI baseline and module versions differ; retry after repository publication completes' >&2; exit 1;
    }
    for tool_arch in "$host_arch" "$arch"; do
        tool_version=$(input_field "tools-$tool_arch" Version)
        test "${tool_version%-*}" = "${module_version%-*}" || {
            echo 'Ubuntu GRUB tools and modules have different upstream releases' >&2; exit 1;
        }
    done
    if [ "$arch" = amd64 ]; then
        test "$(input_field bios Version)" = "$(input_field tools-amd64 Version)" || {
            echo 'Ubuntu BIOS modules and tools have different versions' >&2; exit 1;
        }
    fi
}

assemble_efi() {
    local mkimage=$inputs/tools-$host_arch/usr/bin/grub-mkimage
    local modules=$inputs/modules/usr/lib/grub/$platform
    local baseline=$inputs/baseline/usr/lib/grub/$platform/monolithic/grub$suffix.efi
    # Keep work available to subsequent staging and the process-wide EXIT trap.
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    python3 assemble-efi.py "$mkimage" "$modules" "$baseline" bootstrap.cfg \
        "$work/grub$suffix.efi" "$work"
}

stage_package_files() {
    local doc=$APKG_STAGE_DIR/usr/share/doc/anduinos-grub
    local service
    cp -a "$tools/usr" "$tools/etc" "$APKG_STAGE_DIR/"
    cp -a "$inputs/modules/usr/." "$APKG_STAGE_DIR/usr/"
    if [ "$arch" = amd64 ]; then
        cp -a "$inputs/bios/usr/." "$APKG_STAGE_DIR/usr/"
    fi
    install -Dm644 "$work/grub$suffix.efi" "$APKG_STAGE_DIR/usr/lib/anduinos-grub/grub$suffix.efi"
    install -m755 scripts/grub-multi-install "$APKG_STAGE_DIR/usr/lib/grub/grub-multi-install"
    mkdir -p "$doc" deploy
    cp "$APKG_INPUTS_DIR/manifest.json" "$work/sbat.csv" "$work/modules.txt" "$doc/"
    cp "$tools/usr/share/doc/grub2-common/copyright" "$doc/copyright"
    # Explicit IncludeFile entries retain apkg's service lifecycle generation.
    for service in grub2-common.service grub-initrd-fallback.service; do
        install -m644 "$tools/usr/lib/systemd/system/$service" "deploy/$service"
    done
}

verify_package_files() {
    local executable
    for executable in usr/sbin/update-grub usr/sbin/grub-install usr/bin/grub-mkimage etc/grub.d/05_debian_theme; do
        test -x "$APKG_STAGE_DIR/$executable"
    done
    test -s "$APKG_STAGE_DIR/usr/share/grub/default/grub"
    if grep -Eq '@(GRUB|DEFAULT)_' "$APKG_STAGE_DIR/usr/share/grub/default/grub"; then
        echo 'Unexpanded GRUB defaults template' >&2; exit 1
    fi
}

# apkg Fetch supplies verified, extracted inputs; this script only assembles files.
# No installation, chroot, container, or upstream maintainer script is needed.
resolve_architectures
validate_input_versions
assemble_efi
stage_package_files
verify_package_files
