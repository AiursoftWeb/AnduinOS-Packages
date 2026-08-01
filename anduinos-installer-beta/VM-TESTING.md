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

## Post-release storage-mode campaigns

The ten-row matrix above remains the release-one erase-disk gate. It is not
expanded speculatively when a storage mode has only a UI or planner prototype.
Each post-release-one milestone in
[`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md) adds a separate destructive campaign
before that mode becomes selectable.

### UEFI/GPT coexistence campaign — Storage 2E in progress

[`tests/vm/coexistence-matrix.json`](tests/vm/coexistence-matrix.json) is a
separate eight-row matrix for Btrfs/ext4, Secure Boot disabled/enabled and
shared/new ESP policy. `tests/vm/run-coexistence-qemu.py` accepts only a
regular qcow2 fixture with an explicit SHA-256, clones it into a new output
directory and refuses to reuse an existing campaign directory. It never
accepts or attaches a host block device.

Example dry run:

```sh
python3 tests/vm/run-coexistence-qemu.py \
  --case amd64-secureboot-btrfs-shared-esp \
  --iso /path/to/AnduinOS.iso \
  --iso-sha256 SHA256_OF_ISO \
  --fixture /path/to/windows-shaped-fixture.qcow2 \
  --fixture-sha256 SHA256_OF_FIXTURE \
  --output /tmp/anduinos-vm/coexistence-btrfs \
  --uefi-code /usr/share/OVMF/OVMF_CODE_4M.secboot.fd \
  --uefi-code-sha256 SHA256_OF_OVMF_CODE \
  --uefi-vars /path/to/windows-shaped-fixture-vars.fd \
  --uefi-vars-sha256 SHA256_OF_PAIRED_VARS
```

The VARS file is not a generic OVMF template. It must be the variable store
paired with the Windows fixture after Windows has created and successfully
booted its own `Boot####` entry. Secure Boot enabled and disabled cases retain
separate pinned VARS fixtures. The runner verifies SHA-256 for the ISO, Windows
qcow2, OVMF CODE and paired VARS before creating an output directory; it also
records QEMU, qemu-img, host kernel and architecture metadata in `case.json`.

Review the command before adding `--execute`. Inside the disposable VM only,
the runner exposes the cloned Windows target as `/dev/vda` and a newly-created
1 GiB qcow2 evidence disk as `/dev/vdb`. It never maps either name to a host
block device. Their virtio serials are `ANDUINOS-COEXISTENCE-TARGET` and
`ANDUINOS-EVIDENCE`. Confirm those identities, initialize only the evidence
disk and keep the plan and before-manifest there so they survive a hard power
cut:

```sh
lsblk --paths --output PATH,SIZE,TYPE,FSTYPE,MODEL
mkfs.ext4 -F -L ANDUINOS_EVIDENCE /dev/vdb
mkdir -p /mnt/anduinos-evidence
mount /dev/vdb /mnt/anduinos-evidence
chmod 700 /mnt/anduinos-evidence
export ANDUINOS_INSTALLER_DESTRUCTIVE_TEST=1
export INSTALLER_LIB=/usr/lib/anduinos-installer-beta
```

The internal planner requires the explicit flag, environment marker, root,
QEMU/KVM and an exact `/dev/vda` target. First inspect stable choices; then
build a plan from the current inventory. Values shown in capitals below must
come from that same inspection output:

```sh
python3 "$INSTALLER_LIB/guided_test_plan_cli.py" \
  --guided-destructive-test inspect \
  > /mnt/anduinos-evidence/choices.json

python3 "$INSTALLER_LIB/guided_test_plan_cli.py" \
  --guided-destructive-test build \
  --disk-stable-id DISK_STABLE_ID \
  --extent-id FREE_EXTENT_ID \
  --filesystem btrfs \
  --esp-partuuid EXISTING_ESP_PARTUUID \
  --output /mnt/anduinos-evidence/plan.json
```

Use `--filesystem ext4` for an ext4 row. Replace
`--esp-partuuid EXISTING_ESP_PARTUUID` with `--new-esp` for a dedicated-ESP
row. The plan generator has no production launcher and emits a passwordless
identity accepted only by the guided destructive-test execution policy.

Capture the external baseline before running the installer. Non-shared
preserved partitions are hashed across their exact full block-device length;
a reused ESP is mounted read-only and hashed per file outside
`EFI/AnduinOS`; the manifest also records partition geometry, identities,
NVRAM entries and boot order. Capture fails before disk writes unless the
paired VARS contains a `Windows Boot Manager` entry pointing to an existing
target-disk ESP PARTUUID and `\EFI\Microsoft\Boot\bootmgfw.efi`:

```sh
python3 "$INSTALLER_LIB/guided_test_evidence_cli.py" capture \
  --guided-destructive-test \
  --plan /mnt/anduinos-evidence/plan.json \
  --evidence /mnt/anduinos-evidence/before.json
sync /mnt/anduinos-evidence
```

The internal executor test capability then requires both opt-ins. Keep
`pipefail` enabled and tee its JSON-lines output to the serial port; the host
power-cut watcher reads the stable boundary markers from `serial.log`:

```sh
set -o pipefail
python3 "$INSTALLER_LIB/executor_cli.py" \
  --guided-destructive-test \
  < /mnt/anduinos-evidence/plan.json \
  2>&1 | tee /dev/ttyS0
```

The public `/usr/bin/anduinos-installer-executor` wrapper rejects arguments and
cannot enable this policy. Never reuse a plan or any command in this section
on physical hardware.

For a completed run, verify before shutting down and again after booting the
same target from the ISO. The verifier rejects topology drift, changed full
partition hashes, any shared-ESP change outside `EFI/AnduinOS`, a missing
AnduinOS loader, changed existing NVRAM entries/order or a wrong AnduinOS boot
entry:

```sh
python3 "$INSTALLER_LIB/guided_test_evidence_cli.py" verify \
  --guided-destructive-test \
  --plan /mnt/anduinos-evidence/plan.json \
  --evidence /mnt/anduinos-evidence/before.json
```

Independent Windows and AnduinOS boots remain separate manual release-gate
observations; a successful manifest verification does not substitute for
either boot.

Start from a Windows-shaped GPT image containing:

- a pre-populated FAT ESP with Microsoft and sentinel files;
- Microsoft Reserved, NTFS data and recovery partitions;
- an eligible unallocated extent.

Capture the complete partition map, partition identities and boundaries, all
pre-existing ESP file hashes, preserved-partition fixtures, UEFI variables and
boot order. After installation require:

1. every preserve-marked partition has the same identity and geometry;
2. every pre-existing ESP entry has the same kind and size, and every file has
   the same bytes;
3. only the selected extent/partition and `EFI/AnduinOS` were written;
4. the shared fallback loader was not replaced;
5. AnduinOS and the preserved system boot independently;
6. a stale or changed partition-map plan is rejected before writes.

Inject failures and hard power cuts around partition creation, formatting,
ESP writes, NVRAM updates and every existing fatal boundary. Test Secure Boot
enabled and disabled, an ESP without sufficient free space, rejected legacy
BIOS coexistence and retry after a partially created AnduinOS target.

The stable power-cut boundary identifiers currently are
`guided-partition-command-N`, `guided-format-efi-system`,
`guided-format-swap`, `guided-format-root`, `guided-boot-files` and
`guided-nvram`. In addition, every fixed executor step emits
`guided-step-STEP-ID`, covering mount, extraction, storage/system
configuration, chroot transitions, initramfs/bootloader work and final
unmount. A boundary has a `before` and `after` phase. For example, add the
following to the host runner invocation to kill QEMU immediately after the
root formatter returns:

```sh
--power-cut-at guided-format-root:after --execute
```

The runner writes `power-cut.json` only after seeing the exact serial marker
and sending `SIGKILL` to QEMU. To inspect the same disk state, repeat the
otherwise-identical host invocation without `--power-cut-at` and add:

```sh
--resume-after-power-cut --execute
```

Resume is refused unless the target, copied UEFI variable store, evidence
disk, case metadata and power-cut record all exist and match the supplied
fixture checksum and case. After recovery, mount `/dev/vdb` read-only. A
partially completed run must either verify as complete or make the stale plan
fail before any additional write; archive the artifacts and reset from the
pinned fixture rather than manually repairing the target. Safe automatic
adoption/retry of partially-created guided targets is still an open Storage
2E release gate.

Every completed QEMU process produces a machine-readable result with final
hashes of the target qcow2, copied UEFI VARS and evidence qcow2. Normal runs
write `run-result.json`, power cuts write `run-result-power-cut.json`, and
recovery runs write `run-result-recovery.json`. These files deliberately keep
`test_passed` as `null`; QEMU exit status is not a release result. Verify that
the retained inputs and artifacts have not changed with:

```sh
python3 tests/vm/verify-coexistence-artifacts.py \
  --output /tmp/anduinos-vm/coexistence-btrfs \
  --result normal
```

Use `--result power-cut` or `--result recovery` for those phases. The command
still reports that manual review is required: retain the guest verification
output, screenshots and explicit observations of independent Windows and
AnduinOS boots.

Before placing an installer build into an ISO, inspect the actual `.deb`, not
only the source manifest:

```sh
python3 scripts/verify-built-package.py /path/to/anduinos-installer-beta.deb
```

This verifies that the private planner/evidence CLIs and their core modules
are present, the public executor still rejects arguments, required runtime
dependencies are declared, no public test-tool launcher exists and no Python
cache entered the package.

### Custom-layout campaign

Cover canonical and custom Btrfs names, ext4 split mounts, duplicate or unsafe
mount paths, a populated non-format root, missing ESP/root roles and layouts
that are bootable but intentionally ineligible for Timeback. Verify that the
installed semantic-role manifest agrees with `fstab`, mounted UUIDs and the
Timeback capability shown before installation.

### Existing-container and array campaign

Use only disposable VM images to construct LVM, mdraid and Btrfs multi-device
fixtures. Bind plans by container/array/filesystem UUID and exact member sets,
then:

- boot with every member present;
- remove each redundant member in turn and boot;
- reject substituted, missing-at-install or unexpectedly degraded members;
- reject an LVM target whose PV, VG or LV identity changed;
- verify initramfs discovers the same member set;
- verify every redundant ESP contains the correct signed chain;
- verify RAID0 is never reported as fault tolerant;
- reject Btrfs RAID56 as a supported system root.

Array creation, encryption layering and hibernation receive additional
campaigns when their designs are approved; passing an existing-array campaign
does not authorize those features.
