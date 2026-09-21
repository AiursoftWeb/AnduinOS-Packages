# Secure Boot transition qualification — 2026-09-21

## Exercised environment

- AnduinOS 2.0.3 amd64 ISO userspace, its signed 7.0.0-31 kernel and initramfs.
- Current source toolkit copied into the isolated guest filesystem.
- QEMU q35/KVM with SMM and OVMF_CODE_4M.secboot.fd.
- A new 10 GiB regular-file disk: GPT, one 256 MiB FAT ESP, one ext4 root.
- Initially empty OVMF VARS: Secure Boot disabled and Setup Mode enabled.
- No host block devices, host firmware variables or host MOK were passed through.

The fixture uses a Python guest init process and starts udev, mounts efivarfs
and the ESP, then calls the real `execute('prepare')` implementation. This
exercises real dpkg integrity checks, PE signature verification, ESP writes,
efibootmgr, key generation and mokutil, not command doubles. GUI flow is covered
separately by the GTK checks.

## Observed sequence

1. The fixture registered `\EFI\AnduinOS\grubx64.efi` and rebooted through
   that actual NVRAM entry. Guest inspection reported `boot_loader=grub`,
   `enabled=false`, `setup_mode=true`, and no local MOK.
2. `execute('prepare')` returned successful boot-chain preparation, key creation,
   DKMS configuration, MOK enrollment scheduling and enrollment timeout setup.
   Inspection then reported `boot_loader=shim`, `enrollment_pending=true`.
3. Hashes of the fixture's `EFI/BOOT/BOOTX64.EFI` and
   `EFI/Microsoft/Boot/bootmgfw.efi` were unchanged by preparation.
4. OVMF booted the newly registered AnduinOS shim entry. MokManager was operated
   through QEMU keyboard input: Enroll MOK → Continue → Yes → 123456 → Reboot.
5. The guest booted with `enrolled=true`, `enrollment_pending=false`,
   `enabled=false`, and `firmware_enable_ready=true`, then powered off.
6. With QEMU stopped, `virt-fw-vars` updated only the disposable VARS file:
   enrolled the OVMF test PK/KEK and Microsoft UEFI db certificates, and enabled
   Secure Boot. It preserved the existing MOK and AnduinOS boot entries.
7. The same disk successfully booted through shim and GRUB into the signed
   kernel and guest userspace. Inspection reported `enabled=true`,
   `setup_mode=false`, `enrolled=true`, `boot_loader=shim`; the guest powered off.

Result: the requested disabled → prepare → enroll → enabled transition passed.
An initial run also established that the default 10-second MokManager timeout
can discard an unattended request. Preparation now requests the installer's
unlimited timeout; the successful run used that behavior.

## Evidence and limits

Local artifacts are under
`/home/anduin/.cache/anduinos-secureboot-vm.73R1Kt/`:

- `guest.py`, `build.py`, `control.py`: fixture construction and guest/QMP logic;
- `serial3.log`: direct-GRUB baseline, real preparation and disabled-state MOK enrollment;
- `serial4.log`: enforced Secure Boot, successful signed-kernel boot and retained MOK;
- `screen.png`: MokManager interaction capture;
- `disk3.raw`, `vars.fd`: resulting disposable guest, not release artifacts.

The Windows path contains a preservation sentinel, not a Windows installation.
Windows Boot Manager entry/order preservation, ambiguity rejection and failure
rollback are additionally exercised by the command-boundary tests. This VM has
no installed DKMS modules; real module rebuild commands and failed rebuilds are
covered separately by unit tests. This is a focused amd64 boot-chain transition
test, not an ARM hardware, full desktop, Windows boot, dbx/SBAT revocation or
power-loss qualification. Source tests cover both amd64 and arm64 installation
command plans in erase, manual and coexistence modes. External USB fallback
boot remains deliberately out of scope.
