# Update Kernel Parameters

Change kernel parameters only for a specific, diagnosed need. A parameter
useful on one machine is not a general performance improvement.

!!! warning "Keep a recovery path"

    Incorrect parameters can prevent booting or disable hardware. If you only
    have SSH access, arrange console access before testing boot changes.
    Do not copy generic ACPI, IOMMU, huge-page or CPU-governor recipes.

## Inspect the current command line

```bash
cat /proc/cmdline
```

This shows the parameters used for the current boot.

## Test for one boot

At the GRUB menu, select the normal boot entry and press **e**. On the line
beginning with `linux`, change only the intended parameter. For example,
remove `quiet splash` to see more boot messages; preserve the kernel path,
root-device arguments and all unrelated options.

Press **Ctrl+x** to boot the edited entry, or **Esc** to discard the edit.
This change is temporary. See the [GNU GRUB menu editor documentation](https://www.gnu.org/software/grub/manual/grub/html_node/Menu-entry-editor.html).

## Make a tested change persistent

Create a separate backup without overwriting an earlier one:

```bash
sudo cp --backup=numbered /etc/default/grub /etc/default/grub.before-kernel-parameters
sudoedit /etc/default/grub
```

Edit `GRUB_CMDLINE_LINUX_DEFAULT`, preserving unrelated parameters. For the
boot-message example, remove only the `quiet` and `splash` tokens from its
existing value. Do not replace the entire value with a machine-specific sample.

Regenerate the GRUB menu:

```bash
sudo update-grub
```

If this reports an error, resolve it before rebooting. When ready, reboot and
check `cat /proc/cmdline` again. Configuration snippets in
`/etc/default/grub.d/` can also affect generated entries; inspect them if the
result differs from your edit. Do not edit generated `/boot/grub/grub.cfg`.

## Undo a change

If the system cannot boot normally, use GRUB's temporary editor to remove the
problematic parameter for one boot. Once running, remove the persistent change
or restore the appropriate backup, then run `sudo update-grub` again.
Reboot and verify the active command line.
