# AnduinOS GRUB

`apkg build --all` produces separate amd64 and arm64 packages from one recipe.
Use apkg with BuildInput and SuiteShortName support (10.0.65 or newer).
On an amd64 or arm64 Linux build host, apkg fetches the latest Ubuntu binaries
from the mapped suite, updates and security pockets, with archive signature and
package hash verification. Install `ubuntu-keyring`, `dpkg`, Python 3 and the
runtime libraries required by that suite's native `grub-mkimage` on the builder.
No Docker, chroot, root privileges or execution of foreign binaries is required.

Both native tool payloads are build inputs. The build runs the host's
`grub-mkimage` but packages only the target's tools. It retains Ubuntu's tools,
configuration, themes, kernel hooks and target modules; only amd64 includes
BIOS modules. Tools and EFI modules come from Ubuntu's separate `grub2` and
`grub2-unsigned` source packages: packaging revisions may differ, but a GRUB
release mismatch fails the build. Host runtime incompatibility also fails;
there is no silent fallback to an arbitrary installed tool.

`assemble-efi.py` reads the matching official unsigned EFI's module list,
font memdisk and SBAT, adds Btrfs/LUKS2/Argon2 and assembles our unsigned EFI.
All upstream SBAT revocation identities are preserved, with `grub.anduinos`
added. Shim verification is not disabled. Missing modules, unexpected binary
layouts or lost output metadata fail the build instead of reducing features.
The old pinned Docker/source/SBAT recipe has been removed (recoverable in Git).

`manifest.json`, `sbat.csv` and `modules.txt` ship in
`/usr/share/doc/anduinos-grub`. These record the actual build inputs; they are
not lock files. The AnduinOS package revision is maintained independently of
Ubuntu's version. Rebuilding with new inputs still requires a new package
revision before publishing an update.

This package owns the GRUB tools and platform modules it replaces. It ships no
private key and never generates a shared signing identity. The Secure Boot
toolkit signs the pristine package image with the machine's unified certificate
and deploys it together with the official shim from `anduinos-shim-signed`.

The toolkit now shares signing/deployment between amd64 and arm64, and the
grub-multi-install hook accepts only the native EFI target. This package requires
the toolkit revision carrying that support. ARM64 installer/ISO integration
and Secure Boot positive/negative boot qualification are subsequent milestones;
static module and deployment tests do not establish firmware boot correctness.

## Phase-one acceptance

From the repository root:

```sh
apkg build --path anduinos-grub --all
apkg build --path anduinos-shim-signed --all
apkg test --path anduinos-grub --profile anduinos-package-release-test
python3 anduinos-grub/tests/check_packages.py
```

Verified on 2026-10-16 with apkg 10.0.65:

- All four amd64/arm64 GRUB/shim debs built on the amd64 Linux host.
- Seven source unit tests and both real-package checks passed (each package
  check covers both architectures). Payload tools match the target ISA;
  BIOS modules occur only in amd64. Official shim/MokManager bytes are unchanged.
- Reassembled EFI contains 127 modules on amd64 and 123 on arm64, including
  Btrfs, LUKS2, Argon2 and peimage. Outputs are unsigned; upstream SBAT, font
  memdisk and the AnduinOS bootstrap config are checked during assembly.
- The fetched ARM64 tool was also executed through the host's existing QEMU
  user emulation with isolated ARM runtime libraries. It produced BOTH target
  EFI images byte-identical to those built by the AMD64 tool. This is not a
  claim of a full native ARM64-host apkg run or a Secure Boot guest boot test.
- Subsequently built all four packages natively on the Resolute ARM64 GX10.
  Both source/artifact test groups passed. Its two GRUB EFI images are
  byte-identical to the AMD64 host's outputs. See the GX10 validation snapshot's
  `packages.log` and `README.md` under
  `/home/anduin/Source/Builds/anduinos-arm-validation-20261016` on that machine.
- No packages were installed, no ESP/MOK state changed, and nothing published.

That acceptance record covers phase one, before native deployment support was
added. The toolkit README documents phase two and its remaining guest boot gates.
