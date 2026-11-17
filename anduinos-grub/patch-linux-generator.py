"""Add the encrypted-root RAM handoff to Ubuntu's existing Linux generator.

Keep Ubuntu's kernel discovery, recovery entries and menu policy. The installer
opts in only after provisioning its encrypted root and machine credential.
GRUB's newc syntax reads that credential at boot; no key-containing initramfs
or archive is generated on disk. Unexpected upstream changes fail the build.
"""

from pathlib import Path
import sys


ANCHOR = '  initrd_path_only_early=\n'
HANDOFF = '''  # AnduinOS encrypted-root RAM handoff: keep this after the normal initrd
  # list is assembled, so kernel updates and recovery entries use the same path.
  # The opt-in is exported by the installer, never enabled for ordinary roots.
  if [ "${GRUB_ANDUINOS_CRYPT_HANDOFF:-}" = y ] && test -n "${initrd}"; then
      anduinos_key=/etc/anduinos-luks/machine.key
      if [ "${GRUB_ENABLE_CRYPTODISK:-}" != y ] \\
          || ! uses_abstraction "${GRUB_DEVICE_BOOT}" cryptodisk \\
          || test -L "$anduinos_key" || ! test -f "$anduinos_key" \\
          || [ "$(wc -c < "$anduinos_key")" -ne 64 ]; then
          echo 'AnduinOS encrypted-root handoff is not ready' >&2
          exit 1
      fi
      anduinos_key=$(make_system_path_relative_to_its_root "$anduinos_key")
      case "$anduinos_key" in
          /etc/anduinos-luks/machine.key|/@root/etc/anduinos-luks/machine.key) ;;
          *) echo 'Unsupported AnduinOS encrypted-root key location' >&2; exit 1 ;;
      esac
      # Keep upstream microcode/early initrds first. Linux accepts a trailing
      # uncompressed cpio archive after the ordinary compressed initramfs.
      initrd_path="${initrd_path} newc:/anduinos-unlock.key:${anduinos_key}"
  fi
'''


def patch_generator(source: str) -> str:
    if source.count(ANCHOR) != 1 or 'GRUB_ANDUINOS_CRYPT_HANDOFF' in source:
        raise ValueError("Ubuntu Linux generator changed; review the RAM handoff patch")
    return source.replace(ANCHOR, HANDOFF + ANCHOR)


if __name__ == '__main__':
    path = Path(sys.argv[1])
    path.write_text(patch_generator(path.read_text()))
