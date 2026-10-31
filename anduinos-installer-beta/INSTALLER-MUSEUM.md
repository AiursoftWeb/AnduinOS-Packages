# The AnduinOS Installer Museum

> **A small exhibition of borrowed machinery, compatibility archaeology, and the long road to owning our installation stack.**

Welcome. The exhibits are real; the humor is retrospective.

AnduinOS once installed itself by persuading Ubuntu's Ubiquity installer that it
was still running inside the system Ubiquity expected. That persuasion grew from
a slideshow replacement into a distributed compatibility layer spanning GRUB,
Casper, desktop launchers, `sudo`, environment variables, AppArmor, Bubblewrap,
package manifests, locale databases, Debian maintainer scripts, and target-side
cleanup hooks.

It worked often enough to ship. It also taught us exactly why an operating
system must own its installer.

This museum preserves that history—not to ridicule the work that kept releases
moving, but to remember the constraints it survived and the engineering lessons
it paid for.

---

## Museum map

| Gallery | Period | Principal exhibit |
| --- | --- | --- |
| I | 2024 | Rebranding a borrowed installer |
| II | 2024–2025 | An installation pipeline scattered across the Live system |
| III | 2025–2026 | Environment-variable whack-a-mole and graphics crashes |
| IV | April 2026 | The Bubblewrap incident |
| V | June 2026 | Packaging the patch stack—and discovering more assumptions |
| VI | July 2026 | The first native GTK4 installer |
| VII | July 2026 onward | The architectural break and the installer we own today |

## Provenance of the collection

The history in this exhibition comes from three repositories:

- The former [`Anduin2017/AnduinOS`](https://github.com/Anduin2017/AnduinOS)
  repository contains 32 distinct commits whose subjects explicitly mention
  Ubiquity or Casper, plus many related changes recorded under GRUB, manifests,
  locales, packaging, and Live-session behavior.
- [`AiursoftWeb/AnduinOS-Packages`](https://github.com/AiursoftWeb/AnduinOS-Packages)
  contains the packaged compatibility layer and the native installer's history.
  The legacy installer packages changed 34 times in the 60 days from 5 June to
  5 August 2026.
- [`AiursoftWeb/AnduinOS-2`](https://github.com/AiursoftWeb/AnduinOS-2)
  records the ISO's transition from the legacy integration to the native
  installer.

The former AnduinOS repository has 2,068 reachable commits, but that number is
not an AnduinOS patch count. Its history includes the upstream
`live-custom-ubuntu-from-scratch` project dating back to 2019. AnduinOS's own
line in that repository begins in August 2023.

---

## Gallery I — The borrowed machine

### July 2024: first, change the signs

The earliest AnduinOS-specific installer work was wonderfully direct:

1. Copy a background and welcome image into `/opt/installer` while building the
   image.
2. Inside the chroot, overwrite files under
   `/usr/share/ubiquity-slideshow`.
3. Remove the temporary directory.

The original exhibit survives in
[`0483129`](https://github.com/Anduin2017/AnduinOS/commit/0483129bb08b9143928a5ba313590c2ddaf4ba25):
*“Copy installer assets to /opt and patch Ubiquity installer.”*

The work soon became a dedicated `20-ubiquity-patch` module:

```sh
rsync -Aavx --update --delete ./slides/ \
    /usr/share/ubiquity-slideshow/slides/
```

At this point, “our installer” still meant Ubuntu's installer wearing AnduinOS
artwork. It was not GTK4, it was not libadwaita, and its application model was
not ours. That was acceptable for a beginning; it was not a sustainable final
architecture.

### About the legendary “ten thousand patches”

The legend contains a numerical truth and an important footnote.

When the integration was packaged in June 2026, its first commit added **172
files and 13,929 lines**. However, 9,266 of those lines were vendored jQuery and
another 1,331 were `jquery.cycle`. Much of the remainder was localized slideshow
content.

So no, engineers did not hand-write 13,929 independent Ubiquity fixes. But yes,
we really did carry a five-digit compatibility exhibit in order to present and
control an installer we did not own. See
[`c00106e0`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/c00106e0fb81e32633c23f5845299f86dd8fe22d).

---

## Gallery II — The installer becomes the entire Live system

Branding was only the entrance. Correct installation soon required coordinating
components far outside Ubiquity itself:

- `/etc/casper.conf` defined the Live identity.
- GRUB supplied `boot=casper`, `only-ubiquity`, OEM mode, username, and hostname
  arguments.
- Casper created the Live user and assembled the session.
- A desktop file and a shell wrapper constructed the environment in which
  Ubiquity ran.
- `filesystem.manifest` and `filesystem.manifest-desktop` decided which
  Live-only packages disappeared from the installed system.
- Ubiquity `target-config.d` hooks repaired or removed anything that leaked
  through the copied squashfs.

The pipeline looked approximately like this:

```text
GRUB command line
        │
        ▼
Casper creates the Live session
        │
        ▼
Desktop launcher assembles a sudo environment
        │
        ▼
Ubiquity copies the squashfs and mutates the target
        │
        ▼
Dual manifests select packages for removal
        │
        ▼
target-config hooks repair the installed system
```

The OEM boot path and dual-manifest machinery are visible in
[`d9df037`](https://github.com/Anduin2017/AnduinOS/commit/d9df0376a15207c2767620b23a3fea97f049d641).

This was the central architectural problem of the Ubiquity era: the installer
had no single owner and no single boundary. A change in any layer could surface
to the user as the same unhelpful diagnosis: **the installer broke**.

---

## Gallery III — Environment-variable whack-a-mole

### Preserve `HOME`

In May 2025, launching through `sudo` lost environment state Ubiquity needed.
The desktop entry was patched to preserve it, including `HOME`. The same repair
had to be propagated across four maintained branches. See
[`4f5bf5a`](https://github.com/Anduin2017/AnduinOS/commit/4f5bf5a3c5e74b7ad027fdfb1d8b7a00db0aa379).

### Do not preserve `HOME`

By April 2026, preserving `HOME=/home/live` for a process running as root had
become part of a different failure: sandboxed GTK image loading observed a
user/home identity that did not agree with the process identity. The variable
we had deliberately preserved now had to be deliberately removed.

### Keep the graphics stack calm

Other exhibits from this room include:

- `NO_AT_BRIDGE=1`, added in
  [`ba6357e`](https://github.com/Anduin2017/AnduinOS/commit/ba6357e4e24a1ca7e4b44f3a9533ae80d7645536)
  to mitigate a possible GTK accessibility bridge crash.
- `LIBGL_ALWAYS_SOFTWARE=1`, added in
  [`70d4bc9`](https://github.com/Anduin2017/AnduinOS/commit/70d4bc9490e76468a1bbeb082c490458aca7cad6)
  after crashes involving NVIDIA, Wayland, and Nouveau.

No graphics card was literally destroyed. The user experience of watching the
display stack fail halfway through an operating-system installation was quite
dramatic enough.

The pattern was clear: without a stable process and privilege boundary, the
launcher became an ever-growing theory about which parts of the Live desktop
must be smuggled through `sudo` and which parts must never cross it.

---

## Gallery IV — Please do not feed the Bubblewrap

### 13 April 2026

One failure combined several independent systems:

- `sudo` removed `PYTHONPATH`, making `/usr/lib/ubiquity` unavailable to Python.
- Ubuntu's AppArmor policy restricted unprivileged user namespaces.
- GTK/libglycin used Bubblewrap while decoding artwork.
- Bubblewrap diagnostics on `stderr` could stall the caller.
- GPU acceleration was unreliable on some Live-session combinations.
- A root process could still carry the Live user's `HOME`.

The first stabilizer physically replaced `/usr/bin/bwrap`:

```sh
mv /usr/bin/bwrap /usr/bin/bwrap.real
exec /usr/bin/bwrap.real "$@" 2>/dev/null
```

That implementation is preserved in
[`d09a77e`](https://github.com/Anduin2017/AnduinOS/commit/d09a77ed2533fe0a494129a0cd1d24338d82fb96).

Two hours later, the permanent replacement was removed and folded into the
installer launcher as an ephemeral intervention:

```sh
xhost +SI:localuser:root

# Temporarily replace bwrap with a wrapper that suppresses stderr.
# Restore the real binary after Ubiquity exits.

export PYTHONPATH=/usr/lib/ubiquity
export LIBGL_ALWAYS_SOFTWARE=1
ubiquity gtk_ui
```

A target-side cleanup hook was added so the launcher would not survive the
squashfs copy into the installed system. The accompanying documentation called
the arrangement **“zero-trace.”** See
[`5aaf4b9`](https://github.com/Anduin2017/AnduinOS/commit/5aaf4b924f60a1e18b2b7b63b197af9d248f1f3a).

The later `anduinos-bwrap-hack` package went one step further:

```sh
/usr/bin/bwrap.real "$@" 2>/dev/null || true
```

This suppressed the diagnostics *and converted every Bubblewrap failure into
success*. It may be the collection's purest example of “make the installation
finish first; understand the wreckage later.”

It was intended for the disposable Live environment. Keeping it out of the
installed target depended on the surrounding manifest and cleanup machinery—
which is exactly the kind of cross-component invariant the native installer was
built to eliminate.

---

## Gallery V — The packaged patch stack

### June 2026: the hacks acquire package metadata

The compatibility layer became two Debian packages:

- `anduinos-installer-config`
- `anduinos-bwrap-hack`

Packaging made ownership and removal more disciplined. Direct overwrites gave
way in places to `dpkg-divert`, `postinst`, `postrm`, and explicit target
cleanup. It did not remove the underlying dependency on Ubiquity's private
assumptions.

Over several days, the history records an unusually concentrated debugging
tour:

1. Ubiquity could not resolve DNS inside the target chroot because
   `/etc/resolv.conf` pointed into an empty `/run`. The Live symlink was replaced
   with a real file, with a target hook tasked to restore it.
2. The next day, that DNS workaround and its restoration hook were removed.
3. Locale variables were omitted because preserving them appeared to force an
   English installation.
4. They were then explicitly preserved because omitting them prevented the
   selected locale from reaching the target.
5. Ubiquity's textual `languagelist` was replaced.
6. We discovered that Ubiquity actually consumed
   `languagelist.data.gz`, so the generated binary data needed a diversion too.
7. `gzip -c` failed against reproducible-build timestamps and became
   `gzip -nc`.
8. A 146-line generator finally made the text and binary language lists derive
   from the same 28-language source.

Selected artifacts:

- [`cc26ca16`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/cc26ca16703393f33f540012c37a911d77edb039):
  DNS and environment workaround.
- [`f2b9eb6b`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/f2b9eb6b877da3e420d5db0029e66f4dd323d976):
  preserve the locale variables after all.
- [`5447a74f`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/5447a74f18989f275ee25ced54b6f37e336d8bcd):
  divert the language lists Ubiquity actually reads.
- [`668dc427`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/668dc427b16678e2d7d721adef050605a5c491c1):
  make gzip output reproducible-build-safe.
- [`d50b2f9f`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/d50b2f9f1c02f572652637f2b1c4611f0488247b):
  replace drifting locale artifacts with a generator.

Input methods and Secure Boot presented the same class of problem. They were
not first-class concepts in an AnduinOS-owned plan. They were behaviors to
inject at the correct point in somebody else's pipeline, then verify through
more hooks and package state.

The patch stack was becoming more professional. The architecture it patched
was not becoming more ours.

---

## Gallery VI — The first native machine

### 13 July 2026

The first native AnduinOS GTK4 installer arrived in
[`e167898e`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/e167898e09c627bc60f67b06c77e00c4c21d1a41):
**11 files and 1,800 new lines**.

This was the decisive product choice—replace Ubiquity—but not yet the final
architecture. Its 444-line `backend.py` directly orchestrated `parted`,
`mkfs`, `unsquashfs`, `chroot`, and `grub-install`. The first version re-executed
the whole GTK application as root, passed UI-shaped data directly into the
backend, and implemented only a single `@` Btrfs subvolume.

One day later,
[`918b493a`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/918b493ad4d1df4a6edff67672a57ef993d120f0)
moved the UI back to the desktop user and prefixed privileged commands with
`sudo`. It was progress, but it still treated privilege as a command-launching
detail rather than an architectural boundary.

This exhibit matters because “native” alone is not enough. Replacing an old
installer with a monolithic root application would have changed the toolkit
without changing the risk model.

---

## Gallery VII — The clean break

### 29 July 2026: rebuild, do not accrete

Sixteen days after the first native implementation, the backend was deliberately
torn down and rebuilt. Commit
[`00abc245`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/00abc245cb94359a277a631d68098a17a55d5878)
removed the monolithic execution path and introduced **5,904 lines** across the
new architecture, design documents, tests, and VM validation machinery.

The essential change was ownership:

```text
┌───────────────────────────────────────────────┐
│ Non-privileged GTK4/libadwaita frontend       │
│                                               │
│ discovers → validates → builds desired state  │
└──────────────────────┬────────────────────────┘
                       │
                       │ immutable, versioned InstallPlan
                       │ no commands, hooks, or plaintext secrets
                       ▼
┌───────────────────────────────────────────────┐
│ Privileged fixed executor                     │
│                                               │
│ re-probes → preflights everything → executes  │
│ executor-owned steps and failure policies     │
└───────────────────────────────────────────────┘
```

The ISO switched from `anduinos-installer-config` to the native installer in
[`dea65f6`](https://github.com/AiursoftWeb/AnduinOS-2/commit/dea65f658741e31d462d33eda99c1fb0e9436a23).

On 5 August, the old integration was removed in
[`cb83bbbe`](https://github.com/AiursoftWeb/AnduinOS-Packages/commit/cb83bbbe4c28555feafefbdab258242898c9375a):
**232 files changed and 14,877 lines deleted** across the retirement commit.

That deletion was not merely cleanup. It was the point at which AnduinOS
stopped maintaining an increasingly accurate impersonation of Ubuntu's old
installation environment.

---

## The main hall — What we own today

The modern installer is not valuable merely because it is newer or prettier.
It is valuable because old failure modes have been converted into explicit
contracts.

| Then: compatibility by implication | Now: behavior by contract |
| --- | --- |
| Ubiquity UI patched and launched inside an approximated Ubuntu session | Native GTK4/libadwaita UI running as the desktop user |
| Casper behavior formed part of the installer | A Dracut Live system with an explicit, package-owned Live contract |
| Privilege inherited through desktop files, `sudo -E`, `xhost`, and environment surgery | A small plan-only root executor behind an explicit Polkit boundary |
| UI-shaped dictionaries and shell commands flowed toward root | An immutable, versioned `InstallPlan` contains desired state, never arbitrary commands or plaintext passwords |
| Failures emerged after whichever layer first touched the target | Architecture, firmware, Secure Boot, disk identity, RAM, source image, and every step are preflighted before the first destructive command |
| Mounts lived in the shared Live-session namespace | The executor creates a private mount namespace and disables propagation |
| Btrfs meant, at most, a filesystem and a single `@` subvolume | Btrfs is a documented system ABI with separate system, home, logs, snapshots, containers, and VM-image roles |
| Secure Boot was adapted through inherited Ubiquity behavior and hooks | Secure Boot is an explicit state machine: key ownership, DKMS signing, initramfs, signed EFI verification, and only then MOK enrollment |
| Locale and input-method policy shadowed Ubiquity's private databases | One validated `languages.json` drives 28 locales, layouts, language packs, and optional input methods without product-specific executor branches |
| Live-only cleanup depended on dual manifests and target hooks | The executor owns a fixed cleanup policy and verifies the resulting target |
| “It completed once” was compelling evidence | Unit, failure-injection, package-contract, and destructive VM matrices define release evidence |

### Storage is now a model, not a pile of commands

The current design binds the selected disk by stable identity and byte size,
re-probes it under privilege, computes a typed write set, and constructs every
destructive command inside the executor. Btrfs is the default, ext4 remains an
explicit classic alternative, and the Btrfs subvolume layout is shared with
snapshots, rollback, recovery, and future storage work.

See [`BTRFS-DESIGN.md`](BTRFS-DESIGN.md) and
[`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).

### Secure Boot is now ordered and verifiable

The installer distinguishes enabled, disabled, unsupported, and indeterminate
firmware states. It creates the key inside the target, verifies the key pair,
configures DKMS, rebuilds initramfs, installs and verifies the
architecture-matched signed EFI chain, and only then schedules enrollment.
Secrets never enter the installation plan or command log.

See [`SECURE-BOOT-DESIGN.md`](SECURE-BOOT-DESIGN.md).

### Input methods are now product policy, not installer folklore

Physical keyboard layout, language packs, and optional input methods are
separate concepts. Rime, Wubi, Cangjie, Chewing, Mozc, Hangul, LibThai, Unikey,
and other maintained choices are described as data. The same validated policy
is consumed by the UI, planner, validator, and executor. Adding an input method
does not require teaching the privileged executor its brand name.

See [`LOCALIZATION.md`](LOCALIZATION.md).

### Installation safety now has a definition

The release matrix covers amd64 and arm64, UEFI and Legacy BIOS where
applicable, Secure Boot enabled and disabled, and both Btrfs and ext4. Passing
means installing from the real ISO, rebooting from the resulting virtual disk,
and verifying the installed system—not merely observing a zero exit status.
Failure injection and power-loss campaigns exercise destructive boundaries.

See [`VM-TESTING.md`](VM-TESTING.md).

---

## Curator's note — Why Ubiquity is not coming back

An archive is not a roadmap.

Keeping Ubiquity as an “alternative” would mean maintaining two storage
engines, two privilege models, two Secure Boot workflows, two localization and
input-method systems, two Live-environment contracts, and two destructive test
matrices. The old path would not be a safe fallback; its lack of ownership was
the reason a fallback became necessary in the first place.

When the native installer is missing a feature or contains a bug, we improve
the native installer. We do not restore a stack whose successful operation once
depended on replacing `/usr/bin/bwrap`, changing `/etc/resolv.conf`, guessing
which environment variables to preserve, and hoping every cleanup hook ran at
the correct moment.

> **Ubiquity's retirement is permanent. Its lessons are not.**

The achievement of today's installer is easy to underestimate because a good
installer is quiet. It validates, explains, performs a dangerous operation,
and leaves behind a bootable system. The absence of drama is the product.

This museum exists to remember how much engineering that silence required.
