# Encrypted-root initramfs handoff

`92anduinos-crypt` bridges a GRUB unlock to the standard Dracut/systemd boot.
It does not create a LUKS device, change keyslots, generate a GRUB menu, or
replace `/init`. Installing this package does not enable disk encryption.

The module is selected only for a reserved, unambiguous crypttab entry:

```text
anduinos-root UUID=<LUKS UUID> none luks,x-initrd.attach
```

The UUID identifies the outer LUKS container. `/etc/fstab` and the kernel's
`root=` identify the filesystem inside that container. Both Btrfs and ext4
use the same handoff; Btrfs alone adds its normal root subvolume options.

The encrypted root stores a random 64-byte machine credential at
`/etc/anduinos-luks/machine.key`, in a separate LUKS slot from human passwords
and recovery keys. A later installer change provisions it. GRUB appends that
file as `/anduinos-unlock.key` using `initrd newc:` after unlocking the root.
The Dracut module never copies either credential into a generated initramfs.
Neither the ESP nor a public recovery volume may hold that credential.

Before udev starts, the module moves the RAM file into systemd's standard
`/run/cryptsetup-keys.d/anduinos-root.key` location with mode 0600. Upstream
systemd-cryptsetup discovers it for the reserved crypttab name. A missing
handoff uses the ordinary disk-password prompt. Root discovery, activation,
mounting and switch_root remain upstream responsibilities.

The initqueue completion and pre-pivot hooks remove the transient files once
the root mapping exists. This is file-lifetime cleanup, not a claim that every
copy in physical memory has been erased. See systemd's [crypttab key acquisition
rules](https://www.freedesktop.org/software/systemd/man/latest/crypttab.html):
`keyfile-erase` does not remove auto-discovered keys, so explicit cleanup is
necessary here.

This first component does not expose an installer encryption option. The
installer, GRUB entry generation, first MOK enrollment, recovery UI and Live
repair integration are separate milestones. Secure Boot being enabled does
not, by itself, prove that an initramfs is authenticated.
