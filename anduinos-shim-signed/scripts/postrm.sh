#!/bin/sh
set -eu
if [ "$1" = remove ]; then
    case "$(dpkg --print-architecture)" in
        amd64) suffix=x64 ;;
        arm64) suffix=aa64 ;;
        *) echo 'Unsupported shim architecture' >&2; exit 1 ;;
    esac
    update-alternatives --remove "shim$suffix.efi.signed" \
        "/usr/lib/shim/shim$suffix.efi.signed.latest"
fi
# Certificates and ESP files are machine state, not disposable package data.
