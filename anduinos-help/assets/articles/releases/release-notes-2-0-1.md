# AnduinOS 2.0.1 Release Notes

AnduinOS 2.0.1 is the first feature update following the declarative rewrite in AnduinOS 2.0.0. It adds an ARM64 image, several new first-party applications, graphical first-boot setup, optional Windows and Xbox integration, and a new compressed-memory default.

## New Features

* **ARM64 Release:** AnduinOS now provides an ARM64 image in addition to AMD64. The OS builder, package sources, debootstrap process, GRUB target selection, ISO metadata, filenames, and checksums are architecture-aware; `base-files` and `plymouth-anduinos` are also built for both architectures. ARM64 media is UEFI-only, uses the correct architecture-specific GRUB target and media layout, and does not include the legacy BIOS boot path used by AMD64.
* **Swap Control:** Added `anduinos-swapcontrol-gtk`, a GTK4/Libadwaita application for inspecting and managing Swap files, Zswap, and Zram. It includes a system dashboard, memory recommendations, reliable persistence controls, hibernation-readiness information, a stress-test page, memory detection, and a localized responsive interface.
* **Offline Why AI:** Added the optional `anduinos-why-ai` package, providing the `why` command with a bundled Gemma 4 E2B Q4_K_M model. It supports interactive prompts, piped standard input, streaming output, configurable context and sampling, correct Gemma control-token formatting and random-seed handling, Vulkan acceleration with automatic CPU fallback, explicit CPU-only operation, and an optional local OpenAI-compatible server. The model package is not preinstalled; the default desktop instead includes `anduinos-why-placeholder`, which explains how to install it. [Learn more](../Applications/Development/Why-AI/Why-AI.md).
* **First-Boot Setup and Welcome Center:** Added `anduinos-oobe` with 28-language localization. It guides users through appearance, privacy and security, updates and mirror selection, applications, shortcuts, NVIDIA drivers, Secure Boot enrollment, Xbox controller support, and regional setup; after first boot it remains available as the AnduinOS Welcome Center. It distinguishes first-boot, live-session, and Welcome Center operation, adapts recommendations for ARM64, offers USTC Ubuntu and Flathub mirrors to Chinese users, and safely records setup completion.
* **Firewall Traffic Audit:** Expanded `anduinos-ufwall-gtk` with a libpcap-backed Audit page for live network traffic and complete connection inspection, reliable rule numbering and address parsing, TCP-state and UDP-port direction classification, and direct rule deletion and refresh.
* **Windows Executable Runner:** Added `anduinos-exe-runner` to launch associated Windows executables and MSI installers through Bottles. It can install and initialize Bottles through Flatpak, create the default bottle, install common and CJK fonts, display localized progress and logs, and recommend native Linux alternatives for recognized installers.
* **Xbox Controller Driver:** Added the optional `anduinos-xbox-controller-driver` package based on xpadneo for Bluetooth Xbox controllers. OOBE can install it, inspect controller status, and coordinate DKMS module signing with Secure Boot.
* **Appearance Controls:** Expanded `anduinos-appearance` with the new Separated taskbar layout, which keeps the Start button at the edge while centering application icons, and added a toggle for the Activities button.

!!! warning "ARM64 Compatibility"

    ARM hardware is highly fragmented. The AnduinOS ARM64 image is platform-agnostic and requires a UEFI-compliant environment. Compatibility on unverified devices is not guaranteed and may require device-specific firmware, device trees, or other manual workarounds.

## Behavior Changes

* **Compressed-Memory Default:** `anduinos-desktop` now recommends `anduinos-swap-config`. It enables one LZ4-compressed Zram device sized to 50% of physical memory, sets `vm.swappiness=100` and `vm.page-cluster=0`, and leaves Zswap available but disabled until the user enables it.
* **Desktop Application Set:** `anduinos-desktop-apps` adds a hard dependency on `anduinos-oobe` and recommends `anduinos-exe-runner` and `anduinos-swapcontrol-gtk`. The `simple-scan` recommendation moves from `anduinos-desktop-core` to `anduinos-desktop-apps` without removing scanning support from the standard desktop.
* **MOTD Privacy and Branding:** The `base-files` package replaces Ubuntu's login banner and help text with AnduinOS information. It also replaces `50-motd-news` with a no-op script, preventing remote MOTD news and promotional content from being fetched during terminal login.
* **ArcMenu All-Apps Behavior:** `gnome-shell-extension-arcmenu` configures the All Apps action to open the complete program list directly.
* **Ubuntu Pro Cleanup:** `anduinos-software-properties-gtk` removes Ubuntu Pro files and integration from the AnduinOS Software & Updates interface.

## Bug Fixes

* **Software & Updates:** Fixed Python 3.14 multiprocessing compatibility in `anduinos-software-properties-gtk`.
* **Plymouth Fallback:** `plymouth-anduinos` now recommends `plymouth-theme-ubuntu-text` so the required `ubuntu-text.so` module is available.
* **Amberol Japanese Metadata:** Added the missing Japanese name, generic name, and search keywords to the Amberol desktop entry shipped by `anduinos-desktop-apps`.
* **Upstream Package Tracking:** Rebuilt `anduinos-software-properties-common`, `anduinos-software-properties-gtk`, and `plymouth-anduinos` with Apkg's conversion of inherited exact-version dependencies into compatible minimum-version constraints, preventing routine upstream security updates from blocking the AnduinOS wrappers.
