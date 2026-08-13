#!/bin/bash
set -e

# dpkg runs prerm before unpacking an upgrade. User authentication policy is
# persistent state, so it must only be detached when the package is actually
# removed or deconfigured.
case "${1:-}" in
    remove|deconfigure)
        ;;
    upgrade|failed-upgrade|*)
        exit 0
        ;;
esac

python3 <<'PY'
import os
import tempfile

for path in ("/etc/pam.d/gdm-password", "/etc/pam.d/sudo"):
    if not os.path.isfile(path):
        continue
    with open(path, encoding="utf-8") as source:
        lines = source.read().splitlines()
    result = []
    index = 0
    while index < len(lines):
        if (
            lines[index].startswith("# Managed by anduinos-yubikey-manager")
            and index + 1 < len(lines)
            and "pam_u2f.so" in lines[index + 1]
        ):
            index += 2
            continue
        result.append(lines[index])
        index += 1
    fd, temporary = tempfile.mkstemp(prefix=".yubikey-prerm-", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(result) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

# The neutral passwordless-sudo policy is shared with the installer and is
# persistent system state, not package-owned YubiKey state. Only remove the
# unpublished legacy helper path; never disable the shared policy here.
legacy_sudoers = "/etc/sudoers.d/90-anduinos-yubikey-manager"
if os.path.isfile(legacy_sudoers) and not os.path.islink(legacy_sudoers):
    os.unlink(legacy_sudoers)
PY
