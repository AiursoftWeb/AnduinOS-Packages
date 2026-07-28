# Milestone 5B destructive test protocol

Milestone 5B is a release gate, not a claim that unit tests make disk
installation safe. A row passes only after an installation from the actual
AnduinOS ISO, a reboot from its virtual disk, and the post-install checks below.

## Safety boundary

`tests/vm/run-qemu.py` is dry-run by default and never accepts a host block
device. With `--execute`, it creates a new `target.qcow2` inside a dedicated
output directory and refuses to overwrite an existing disk. Do not attach host
disks, `/dev/disk/by-id` paths, shared writable directories, or USB devices to
these VMs.

The installer's production validation deliberately rejects loop devices.
Destructive storage tests therefore run inside a VM against `/dev/vda`; they
must not weaken production disk validation.

## Required matrix

[`tests/vm/matrix.json`](tests/vm/matrix.json) defines ten release-one rows:

- amd64 Legacy BIOS with Btrfs and ext4;
- amd64 UEFI with Secure Boot disabled/enabled and both filesystems;
- arm64 standards-based UEFI/ACPI with Secure Boot disabled/enabled and both
  filesystems.

Secure Boot rows require a Secure-Boot-capable read-only firmware image and a
fresh writable variable-store template with platform keys enrolled. Merely
booting with UEFI firmware does **not** prove Secure Boot is enabled. Confirm
`mokutil --sb-state` in both the live environment and installed system.

Example dry run:

```sh
python3 tests/vm/run-qemu.py \
  --case amd64-secureboot-btrfs \
  --iso /path/to/AnduinOS.iso \
  --output /tmp/anduinos-vm/amd64-secureboot-btrfs \
  --uefi-code /usr/share/OVMF/OVMF_CODE_4M.secboot.fd \
  --uefi-vars /usr/share/OVMF/OVMF_VARS_4M.ms.fd
```

Review the printed command, then add `--execute`. Use architecture-matching
firmware for arm64.

## Pass criteria for every row

1. The live environment reports the expected architecture, firmware mode and
   Secure Boot state.
2. The UI identifies only the fresh 32 GiB virtual target disk and requires
   the final destructive confirmation.
3. Installation reaches completion without a shell traceback.
4. The ISO is detached and the installed disk boots independently.
5. `/` has the selected filesystem. Btrfs rows contain the complete subvolume
   ABI from `BTRFS-DESIGN.md`; ext4 rows contain no Btrfs subvolume mounts.
6. The EFI System Partition is mounted at `/boot/efi`; amd64 BIOS rows also
   boot through GRUB BIOS without depending on EFI NVRAM.
7. A 4 GiB disk swap is active at priority 10. zram uses LZ4, 50% of RAM and
   priority 100.
8. The created user can log in and use sudo; locale, timezone, keyboard,
   hostname and machine-id are correct.
9. No live-session-only packages, mounts, DNS files or `policy-rc.d` remain.
10. Kernel, initramfs and GRUB artifacts agree. The fallback EFI loader exists
    for UEFI rows.

For Secure Boot rows, also require:

1. shim and GRUB verify as signed before enrollment is scheduled;
2. the first reboot enters MOK Manager;
3. password `123456` enrolls the generated AnduinOS MOK;
4. the following boot succeeds with Secure Boot still enabled;
5. `mokutil --test-key` recognizes the enrolled certificate;
6. every installed DKMS module verifies against that certificate.

## Failure and power-loss campaign

Run each filesystem at least once with failure injected immediately before and
after partitioning, formatting, mounting, squashfs extraction, target
configuration, initramfs generation, bootloader installation, MOK scheduling
and final unmount.

Process-level injected failures must leave all installer-owned mounts
unmounted. A hard VM power cut has no cleanup opportunity; after reboot, the
installer must either reject unexpected state before destruction or safely
restart the explicit erase-disk operation after a new confirmation. Never
promise resume semantics for release one.

Retain `case.json`, `serial.log`, screenshots, installer log and the exact ISO
checksum for every run. A row is not passed solely because QEMU returned zero.
