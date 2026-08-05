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
   anduinos-waypoint-gtk` is clean.
3. Run:

   ```bash
   sudo /usr/share/doc/anduinos-waypoint-gtk/qualify-recovery-vm.sh preflight
   /usr/bin/anduinos-waypoint-cli status
   for target in / /home /var/log /.snapshots \
       /var/lib/containers /var/lib/libvirt/images; do
       findmnt -no SOURCE,FSTYPE,FSROOT --target "$target"
   done
   mokutil --sb-state
   ```

4. Take a powered-off hypervisor snapshot named `waypoint-clean`. Every
   destructive lane starts from this snapshot.

The helper refuses physical machines, containers, incomplete layouts, and
implicit destructive execution. Its state lives below `/var/log` on `@log`,
which is intentionally outside system recovery points.

## Cancellation before reboot

```bash
sudo /usr/share/doc/anduinos-waypoint-gtk/qualify-recovery-vm.sh \
    test-cancel I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM
```

Pass criteria:

- the pending transaction and one-shot GRUB variable are cleared;
- both target and fallback records return to `ready`;
- normal boot remains selected; and
- rebooting does not change the current root.

## Normal rollback and confirmation

```bash
sudo /usr/share/doc/anduinos-waypoint-gtk/qualify-recovery-vm.sh \
    prepare-rollback I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM
sudo reboot
# After the graphical system is fully online:
sudo /usr/share/doc/anduinos-waypoint-gtk/qualify-recovery-vm.sh verify-rollback
```

Pass criteria:

- the pre-snapshot marker is restored;
- the selected deployment becomes `current`;
- the pending transaction disappears only after userspace confirmation;
- the old writable root is deleted after confirmation, not before it;
- the fallback record remains a valid `ready` recovery point;
- `update-grub` succeeds without adding operating systems from other disks; and
- another ordinary reboot remains on the confirmed root.

Repeat this lane three times, including once after an APT upgrade creates its
paired automatic recovery points. Also run create, verify, external export,
external verification, import, and delete before scheduling one imported point.

## Hard power-loss matrix

The initramfs binary prints `WAYPOINT-CHECKPOINT <name>` only after the
corresponding state and filesystem boundary has been synchronized. Capture the
VM serial console. For each checkpoint below, restore `waypoint-clean`, prepare
a rollback, reboot, and hard-stop the VM immediately after that line appears.
Do not use an orderly shutdown.

| Apply boundary | Expected result after powering on twice |
| --- | --- |
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
/usr/bin/anduinos-waypoint-cli status --json
sudo btrfs subvolume list /
sudo grub-editenv /boot/efi/EFI/anduinos/waypoint-grubenv list
sudo journalctl -b -1 -b 0 -u anduinos-waypoint-confirm.service --no-pager
sudo journalctl -b -1 -b 0 -k --no-pager | grep -F WAYPOINT-CHECKPOINT
```

The lane passes only if the machine boots without manual repair, the pending
transaction is eventually removed, exactly one deployment is `current`, the
interrupted target is `failed-reverted`, the fallback is `current`, and no
staging or `@root.waypoint-*` subvolume is orphaned. Preserve the console and
JSON logs as release evidence.

## Failure lanes

From fresh hypervisor snapshots, separately qualify a full filesystem, missing
recovery root, damaged metadata, missing kernel, missing initramfs, forced GRUB
generation failure, and an ext4 installation. Mutations must fail closed before
arming a restore. The ext4 system must remain read-only in the application and
must never create `/.snapshots` as a substitute layout.

Run the normal, cancellation, and power-loss lanes with virtual Secure Boot both
enabled and disabled. With Secure Boot enabled, an unsigned kernel or an
unenrolled DKMS signing key must be rejected before GRUB is armed.
