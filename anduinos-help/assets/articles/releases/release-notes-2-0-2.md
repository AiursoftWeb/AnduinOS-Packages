# AnduinOS 2.0.2 Release Notes

AnduinOS 2.0.2 is a broad system update focused on installation, Btrfs recovery, hardware management, first-boot setup, desktop consistency, and long-term maintainability.

Some storage-layout and live-image changes apply only to new installations. Existing AnduinOS 2 systems continue to receive compatible application, desktop, security, and packaging updates through APT.

## New Features

* **Native AnduinOS Installer:** Made `anduinos-installer-beta` the default AnduinOS installation path and retired the Ubiquity-based installer stack. It separates the unprivileged interface from a restricted privileged executor and provides guided disk setup, coexistence installation, separate target- and boot-disk selection, a clear review and preflight step, automatic mirror selection, a fully offline path, asynchronous Wi-Fi handling and profile migration, isolated keyboard previews, multiple declarative input methods, validated account and machine identity, UTC and localized timezone handling, GRUB 2.14 EFI support, low-battery protection, streamed diagnostics, safe retry and mount cleanup, optional multimedia codecs, and optional third-party drivers.
* **Btrfs Installation Layout:** New installations use Btrfs by default with separate subvolumes for the operating system, home directories, logs, recovery data, containers, and virtual machine images. Unencrypted Ext4 remains available as a classic alternative, and Swap is sized dynamically while preserving at least 20 GiB for the system.
* **Disk Snapshots Manager:** Added `anduinos-btrfs-snapshots-manager`, a native graphical product for immutable Btrfs system snapshots and independent Personal Files history. It supports manual and scheduled snapshots, enforced retention policies, protected snapshots, storage visualization, export, rollback reconciliation, cross-disk recovery and discovery, interrupted-operation recovery, Btrfs maintenance, a read-only S.M.A.R.T. disk-health view, and Nautilus File History actions for earlier or deleted files. Its authorization distinguishes personal-file operations from system recovery and its interface reports storage use, cleanup results, pending size, and operation priority.
* **Driver Center:** Added `anduinos-driver-center`, a central graphical product for inspecting, installing, and repairing graphics, audio, printing, Xbox controller, DKMS, and Secure Boot support. It can apply fixed, hardware-aware recovery actions and manage supported device firmware through fwupd without exposing unrestricted root access to the interface.
* **Secure Boot Toolkit:** Added shared tools for enrolling security certificates, checking module signatures, and recovering Secure Boot. When DKMS is already installed, these tools can also sign DKMS modules. The toolkit does not install DKMS or a compiler toolchain on systems that do not otherwise need them.
* **YubiKey Security Center:** Added `anduinos-yubikey-manager`, a graphical product for YubiKey-backed GDM login, sudo authentication, passwordless-sudo safeguards, resident SSH credentials, persistent SSH configuration, Passkey guidance and Yubico Authenticator integration, and Git SSH commit signing.
* **Bash Command Suggestions:** Added `anduinos-bash-guess-command`, a fast, fully offline ghost-text suggestion product for Bash. It learns from local command history, understands common developer tools, uses offline APT metadata and command popularity to improve ranking, and accepts suggestions with Right Arrow or End without changing native Enter or Tab behavior. It handles history edge cases and terminal resizing without duplicate prompts or colored diagnostic noise, and explicitly tracks its native and Rust entry points so upgrades cannot leave mismatched components behind.
* **Improved First-Boot Networking:** Expanded `anduinos-oobe` with connectivity-aware Wi-Fi and Ethernet setup, hardened Flathub mirror switching, and Nextcloud guidance. OOBE and `anduinos-ufwall-gtk` also gain graphical mDNS controls.
* **Desktop Icons Control:** Added a Desktop Icons switch to `anduinos-appearance`.
* **Automatic Theme Synchronization:** Added `anduinos-theme-sync`, a lightweight service that propagates GNOME light and dark preferences to Flatpak GTK3 applications.
* **Graphical Firmware Updates:** On Resolute, `anduinos-appstore` now recommends `gnome-software-plugin-fwupd`, allowing supported device firmware managed by fwupd to appear in GNOME Software.
* **AnduinOS Applications in the Store:** Added validated AppStream metadata for `anduinos-appearance`, `anduinos-btrfs-snapshots-manager`, `anduinos-driver-center`, `anduinos-exe-runner`, `anduinos-installer-beta`, `anduinos-oobe`, `anduinos-swapcontrol-gtk`, `anduinos-ufwall-gtk`, and `anduinos-yubikey-manager` so these applications can be discovered and managed through GNOME Software.
* **Expanded Swap Control:** Expanded `anduinos-swapcontrol-gtk` with partition-backed Swap support, installer-managed Swap detection, improved hibernation-readiness checks, transactional Swap-file changes, and a responsive narrow-window layout.
* **Optional Multimedia Bundle:** Added `anduinos-multimedia-codecs` as one optional package for the GStreamer bad/ugly/libav plugins and additional FFmpeg codecs.
* **Apkg Distribution:** Added the `apkg` package to the AnduinOS package set so the declarative package toolchain can be installed and updated through APT while using the system-provided .NET 10 and ASP.NET Core runtimes.
* **Rime Candidate Filtering:** Added Lua-based candidate filtering to `anduinos-rime`.

## Behavior Changes

* **VMware Guest Integration:** AMD64 Live images now include `open-vm-tools-desktop`, enabling dynamic display resizing and improving clipboard and drag-and-drop integration in VMware. The native installer retains the VMware guest packages on VMware targets and purges them, together with orphaned dependencies, from other targets; `anduinos-desktop-core` suggests the integration without installing it universally on physical systems.
* **Installer Identity and Access Policy:** The native installer requires a non-empty account password and validates the username, password, and machine name before it creates the immutable installation plan. Machine names follow traditional ASCII hostname-label rules; mixed-case input such as `TT-VIEW-71` is accepted and stored in canonical lowercase form as `tt-view-71`. Passwordless sudo, automatic desktop login, and SSH password login are independent Advanced Options and are all disabled by default.
* **Declarative Target Composition:** Persistent metapackages own capabilities required by the installed system, while the Live image declares temporary and new-install-only payloads. The installer can therefore remove Live-only packages and purge orphaned dependencies without reconstructing the desktop package set procedurally, while retaining explicit target-specific capabilities such as VMware integration when applicable.
* **Architecture-Aware Boot Composition:** `anduinos-core-system` owns the architecture-matched GRUB modules needed after installation. AMD64 systems retain both the `i386-pc` modules for legacy BIOS and the `x86_64-efi` modules for UEFI, while ARM64 systems use the ARM64 UEFI stack. The new `anduinos-grub-style` package owns the installed system's GRUB presentation defaults. The ISO builder declares its signed GRUB and shim build dependencies explicitly, and the installer verifies required packages after target cleanup and checks the selected GRUB platform modules before installing the bootloader.
* **Resolute Kernel Policy:** Desktop installations use the Ubuntu 26.04 HWE kernel line and install `anduinos-kernel-parameters` to enable `preempt=full` on the generic kernel. The package conflicts with low-latency kernels and marks the system for reboot when necessary.
* **OOBE Packaging:** `anduinos-desktop-apps` now recommends `anduinos-oobe` instead of declaring it as a hard dependency, reducing the chance that an optional first-boot component blocks a base desktop upgrade.
* **OOBE Navigation:** Launching Driver Center keeps OOBE on the Driver page, Xbox setup is intentionally simpler, and taskbar selection shares its implementation with AnduinOS Appearance.
* **A Tidier App List:** For new user accounts, system tools such as Driver Center, Disks, Logs, Firewall, Swap Control, and Disk Snapshots Manager are collected in the **System** folder. Helper entries used only for opening files or links no longer appear as separate apps. This removes confusing or duplicate icons without removing EXE Runner, Firefox integration, or driver-management features.
* **A More Useful Start Menu:** The Start menu now opens with pinned and frequently used apps and can show up to 16 frequent apps. Its height adjusts to the smallest connected display, including displays using Wayland scaling, so the menu is less likely to extend beyond the screen.
* **Improved Light Lock Screen:** The light lock screen now has clearer text, password fields, user pictures, and notifications while remaining compatible with Blur My Shell.
* **Clearer Software Sources:** Ubuntu and AnduinOS repositories are identified as official sources in the software store, making them easier to distinguish from third-party repositories.
* **Language-Specific Input Methods:** Rime is no longer installed on every system. The installer selects it for the languages that need it, while upgrades preserve Rime and its user settings when it is already installed.
* **Optional Multimedia Codecs:** Additional GStreamer and FFmpeg codecs are now grouped into the optional `anduinos-multimedia-codecs` package. New users can select them in the installer. An upgrade does not deliberately remove codecs that are already installed, although APT may list automatically installed, unused codecs as removable later.
* **Driver Management:** Driver Center replaces the older Software & Updates driver interface as the recommended place to install and repair drivers.
* **Safer Windows File Handling:** EXE Runner now opens only Windows executables and MSI installers. It no longer registers itself for ordinary Linux programs, preventing it from taking over unrelated files.
* **Windows Application Icons:** Windows executables can show their embedded icons in Files when the optional icon thumbnailer is installed.
* **Safer Rime Upgrades:** Rime defaults are now layered on top of Ubuntu's files instead of replacing them. This preserves user customizations and avoids conflicts during upgrades.
* **Unified Live-Session Region Settings:** The old late-running timezone script and its manual `debconf` update have been removed. The Live system now applies the language and timezone selected in the boot menu through one Casper startup contract: Casper handles the language, while the tested `anduinos-live-settings` hook handles the timezone. As a result, the Live desktop and installer receive the same regional choices from the beginning of the session, and invalid boot arguments are safely ignored.
* **Cleaner Live Image:** Build tools and obsolete installer components are no longer left in the ISO or installed system. The installer also removes old Live-only packages after their responsibilities have moved into the native installer.
* **Better Virtual-Machine Boot Graphics:** Plymouth is free to select an appropriate resolution instead of being forced to use GRUB's resolution.

### Packaging and Release Engineering

The following changes are mostly invisible during everyday use, but make upgrades and installation media safer to maintain:

* Packages shared with AnduinOS 1.x continue to target Noble, while AnduinOS 2-only components target Resolute. Obsolete Questing-specific branches have been removed.
* GRUB-related package scripts detect image-building environments and postpone `update-grub` until it is safe to run.
* **Offline AI Packaging:** Completed the model license and attribution information, added SHA-256 verification for the bundled model, and reduced unnecessary dependencies by using `llama.cpp-tools`.
* Every release ISO is exercised in disposable QEMU virtual machines. The release test installs the graphical system using BIOS, UEFI, and Secure Boot; covers online and offline installation, Btrfs and Ext4, MOK enrollment, snapshots, and SSH policy; and retains logs, screenshots, and machine-readable results.

### SSH Server Configuration Changes

* **Available on New Installations:** New AnduinOS 2.0.2 installations include `openssh-server`, allowing the Secure Shell switch in GNOME Settings to work without installing another package. Both `ssh.service` and `ssh.socket` remain disabled by default, so a newly installed system does not listen on port 22 unless the user explicitly enables remote access. See [Enable SSH](../Install/Enable-SSH.md) for the graphical and command-line workflows.
* **Installer Choice:** The installer's Advanced Options page can enable SSH password login during installation. Leaving the option off keeps SSH disabled initially without removing the server, so it can still be enabled later from GNOME Settings.
* **Unique Machine Identity:** The Live image does not retain an active SSH listener or reusable host identity. During installation, the installer removes any Live-session SSH host keys, generates new keys owned by the installed machine, creates the transient `/run/sshd` prerequisite inside the target's isolated runtime environment, validates the resulting `sshd` configuration, and applies the selected initial SSH state.
* **Existing Systems Without OpenSSH:** A normal upgrade does not automatically install or start `openssh-server` on an existing AnduinOS system. Users who want the GNOME Secure Shell switch can install it explicitly with `sudo apt install openssh-server`.
* **Existing SSH Installations:** Systems that already have OpenSSH keep their administrator-selected enabled or disabled state during the upgrade. Existing SSH services and active remote administration are not intentionally reset by the desktop package transition.
* **Firewall Preparation:** New installations prepare the standard OpenSSH application rule when UFW is available without starting an SSH listener. This allows a later explicit opt-in through the installer or GNOME Settings to work with the firewall enabled.

## Bug Fixes

* **OOBE Offline Startup:** Fixed OOBE failing to start correctly without Internet access.
* **OOBE Secure Boot Text:** Restored missing Secure Boot translations.
* **ARM64 USB Images:** Fixed the GPT and EFI System Partition layout used by ARM64 USB media.
* **Hong Kong Fonts:** Fixed Hong Kong serif text falling back to the Taiwan CJK font variant.
* **GRUB Multilingual Rendering:** Fixed missing glyph coverage for several non-Latin boot-menu languages.
* **Live GRUB State:** Fixed GRUB initrd fallback and bookkeeping services running in `boot=casper` sessions, which could allow temporary live-session boot state to contaminate the installed system.
* **Swap Control Dependencies:** Fixed `anduinos-swapcontrol-gtk` to declare `anduinos-swap-config` as a recommendation.
* **Appearance Dependencies:** Added the missing `python3-gi-cairo` runtime dependency to `anduinos-appearance`.
* **Font Packaging:** Fixed licensing and packaging metadata in `anduinos-fonts`.
