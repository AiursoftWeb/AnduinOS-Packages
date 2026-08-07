# Recovery VM qualification

This matrix is a release gate, not a development demonstration. Run it only on
a disposable virtual machine installed by the current AnduinOS installer onto
one virtual disk. Do not attach host filesystems or irreplaceable data.

The loopback tests qualify real Btrfs operations without rebooting. They cannot
qualify GRUB, initramfs root replacement, userspace confirmation, automatic
fallback, Secure Boot, or power loss. Those properties require this matrix.

## Baseline

1. Install the APKG-built amd64 Deb in a UEFI VM. Enable virtual Secure Boot for
   the Secure Boot lane; keep a second lane with it disabled.
2. Confirm that the VM boots normally twice and that `dpkg -V
   anduinos-btrfs-snapshots-manager` is clean.
3. Run:

   ```bash
   sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh preflight
   /usr/bin/anduinos-btrfs-snapshots-manager-cli status
   for target in / /home /var/log /.snapshots \
       /var/lib/containers /var/lib/libvirt/images; do
       findmnt -no SOURCE,FSTYPE,FSROOT --target "$target"
   done
   mokutil --sb-state
   ```

4. Take a powered-off hypervisor snapshot named `snapshots-manager-clean`. Every
   destructive lane starts from this snapshot.

Add a UEFI-without-Secure-Boot-support lane. Recovery validation must skip
signature requirements only when the toolkit explicitly reports `unsupported`;
an `unknown` toolkit state or an older boolean-only status schema must fail
closed.

The helper refuses physical machines, containers, incomplete layouts, and
implicit destructive execution. Its state lives below `/var/log` on `@log`,
which is intentionally outside system snapshots.

## Cancellation before reboot

```bash
sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh \
    test-cancel I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM
```

Pass criteria:

- the pending transaction and one-shot GRUB variable are cleared;
- both target and fallback records return to `ready`;
- normal boot remains selected; and
- rebooting does not change the current root.

## Normal rollback and confirmation

```bash
sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh \
    prepare-rollback I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM
sudo reboot
# After the graphical system is fully online:
sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh verify-rollback
```

Pass criteria:

- the pre-snapshot marker is restored;
- the selected deployment becomes `current`;
- the pending transaction disappears only after userspace confirmation;
- the old writable root is deleted after confirmation, not before it;
- the fallback record remains a valid `ready` system snapshot;
- `update-grub` succeeds without adding operating systems from other disks; and
- another ordinary reboot remains on the confirmed root.

Repeat this lane three times, including once after an APT upgrade creates its
pre-transaction automatic system snapshot. Also exercise create, verify, browse,
single-file recovery, and delete before scheduling another local snapshot.

## Docker autoremove regression

This is the release regression for a package database and executable disappearing
after a purported rollback. Restore `snapshots-manager-clean`, install the distro
`docker.io` package, and run:

```bash
sudo apt-get install docker.io
sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh \
    prepare-docker-autoremove I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM
sudo reboot
sudo /usr/share/doc/anduinos-btrfs-snapshots-manager/qualify-recovery-vm.sh \
    verify-docker-autoremove
```

The preparation command creates the selected snapshot before running
`apt-get autoremove --yes docker.io`, proves that both the installed package and
`/usr/bin/docker` are absent, then arms the rollback. The lane passes only when
the exact package version and executable return, the selected deployment is
confirmed `current`, the pending transaction is gone, and its terminal history
contains at least one initramfs entry and the final synchronized checkpoint.

## Hard power-loss matrix

The initramfs binary prints `SNAPSHOTS-MANAGER-CHECKPOINT <name>` only after the
corresponding state and filesystem boundary has been synchronized. Capture the
VM serial console. For each checkpoint below, restore `snapshots-manager-clean`, prepare
a rollback, reboot, and hard-stop the VM immediately after that line appears.
Do not use an orderly shutdown.

| Apply boundary | Expected result after powering on twice |
| --- | --- |
| `initramfs-entered` | The durable entry proves the premount hook ran; retry remains bounded |
| `validating` | No root has moved; retry validates again or fails safely |
| `apply-started` | A bootable target or protected fallback; never a missing `@root` |
| `writable-target-created` | Automatic fallback completes and removes the unused target root |
| `current-root-protected` | Automatic fallback restores the protected current root |
| `target-root-activated` | Automatic fallback restores the protected current root |
| `booted-unconfirmed-recorded` | The next unrequested boot reverts to the protected fallback |

To qualify interruption during fallback, first interrupt an apply boundary.
Power on, then hard-stop again at each revert checkpoint:

| Revert boundary | Expected result on the following boot |
| --- | --- |
| `revert-started` | Revert resumes idempotently |
| `restored-root-moved-aside` | Fallback is activated; no root is lost |
| `fallback-root-activated` | Discarded restored root is cleaned up |
| `discarded-root-deleted` | Reverted state is committed |
| `reverted-recorded` | Userspace records fallback as `current` and clears the transaction |

After every interrupted lane, collect:

```bash
/usr/bin/anduinos-btrfs-snapshots-manager-cli status --json
sudo jq . /.snapshots/anduinos-btrfs-snapshots-manager/transactions/pending-rollback.json
sudo btrfs subvolume list /
sudo grub-editenv /boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv list
sudo journalctl -b -1 -b 0 -u anduinos-btrfs-snapshots-manager-confirm.service --no-pager
sudo journalctl -b -1 -b 0 -k --no-pager | grep -F SNAPSHOTS-MANAGER-CHECKPOINT
```

The lane passes only if the machine boots without manual repair, the pending
transaction is eventually removed, exactly one deployment is `current`, the
interrupted target is `failed-reverted`, the fallback is `current`, and no
staging or legacy-compatible `@root.snapshots-manager-*` subvolume is orphaned. Preserve the console and
JSON logs as release evidence.

Also qualify a deliberately broken premount hook in a disposable clone: leave the
recovery kernel command-line request intact but prevent the hook from invoking the
engine. If userspace is reached, confirmation must record a terminal `failed`
transaction with the “without entering the initramfs” diagnostic, return target and
fallback metadata to `ready`, clear the EFI recovery lease, and preserve the JSON
under `rollback-history`. It must never report a successful rollback.

## Failure lanes

From fresh hypervisor snapshots, separately qualify a full filesystem, missing
recovery root, damaged metadata, missing kernel, missing initramfs, forced GRUB
generation failure, and an ext4 installation. Mutations must fail closed before
arming a restore. The ext4 system must remain read-only in the application and
must never create `/.snapshots` as a substitute layout.

Run the normal, cancellation, and power-loss lanes with virtual Secure Boot both
enabled and disabled. With Secure Boot enabled, an unsigned kernel or an
unenrolled DKMS signing key must be rejected before GRUB is armed.
