# AnduinOS 2.0.0 Release Notes

## The Declarative Revolution

AnduinOS v2.0.0 marks a substantial redesign of how the operating system is built, distributed, and maintained. The new architecture responds to community feedback about maintainability and package management while preserving the familiar AnduinOS desktop experience.

This release introduces a more open and maintainable distribution-engineering model. Its source code and packaging pipelines are available in [AnduinOS-2 (OS Builder)](https://github.com/aiursoftweb/anduinos-2), [AnduinOS-Packages (Package Sources)](https://github.com/aiursoftweb/anduinos-packages), and [Apkg (Declarative Build Tool)](https://github.com/aiursoftweb/apkg).

AnduinOS-authored source code in the AnduinOS 2 builder and package repositories is licensed under GPL-3.0. Apkg is licensed under the MIT License. Third-party components retain their respective licenses.

### The Declarative Architecture

* **Declarative System Configuration:** The previous imperative configuration pipeline has been replaced with package declarations assembled through `debootstrap` and `chroot`. Although builds are not strictly bit-for-bit reproducible, this approach makes system state easier to inspect and reduces configuration drift and script-ordering failures.
* **Introducing [`aosproj` and `apkg`](https://apkg.aiursoft.com/):** AnduinOS uses a custom XML-based domain-specific language, `aosproj`, to describe package contents and system state. Apkg validates these declarations and compiles them into standard native `.deb` packages.
* **The `AnduinOS-Packages` Repository:** The system core is now modularized into 56 standalone packages across three tiers: Hard Replacements (e.g., overriding `ubuntu-desktop`), Soft Overrides (e.g., `apt-config`), and Branding/Capability extensions.
* **Native APT Upgrade Path:** Custom updaters (`do_anduinos_upgrade`, `do-anduinos-autorepair`) are retired. AnduinOS 2 uses the standard `sudo apt update && sudo apt upgrade` workflow and remains integrated with Ubuntu's package-management ecosystem.
* **Dracut Compatibility:** AnduinOS packages were validated with the `dracut` initramfs framework and support both `initrd` and `initrd.gz` naming conventions.
* **Optimized Package Delivery:** Fluent themes are packaged as direct file-extraction packages, `anduinos-core-system` is split into `anduinos-container` and `anduinos-core-system` for container readiness, and extension `dconf` updates are consolidated into fewer `postinst` operations.

### Global Infrastructure & Project Stewardship

* **Maintained by [AIURSOFT LIMITED](https://github.com/AiursoftWeb):** AnduinOS is now officially maintained by AIURSOFT LIMITED (Hong Kong), providing an organizational home for the project and its open-source ecosystem.
* **Global CDN Package Network:** AnduinOS APT repositories have migrated to [`packages.anduinos.com`](https://packages.anduinos.com/), with Cloudflare load balancing across nodes in the United States, Europe, and Asia. Repository definitions also specify `[arch=amd64]` explicitly to avoid unintended multi-architecture dependency resolution.

### Base System & Performance Tuning

* **Updated Foundations:** Transitioned the system base from Ubuntu 25.10 (Questing) to **Ubuntu 26.04 (Resolute)** and its **Linux 7** kernel line for updated hardware and graphics support.
* **Desktop-Optimized Kernel Parameters:** Adjusted several upstream defaults for desktop responsiveness and latency:
  * **Memory:** Adjusted `vm.swappiness=10` and `vm.vfs_cache_pressure=50` for better responsiveness.
  * **Disk I/O:** Set `vm.dirty_background_ratio=5` and `vm.dirty_ratio=10` to reduce long writeback stalls during heavy disk activity.
  * **Network:** Enabled BBR congestion control and `tcp_fastopen=3` to improve network performance where supported.
  * **Workloads:** Raised `fs.inotify` limits to 524288 for developer-heavy file-watching tasks.
* **Intel SOF Audio Support:** Shipped `firmware-sof-anduinos` to provide updated Intel SOF audio firmware in a package compatible with the system's Secure Boot workflow.

### The "Single ISO" Multilingual Experience

* **Runtime Language Selection:** We shifted our localization strategy from "build-time forking" to "runtime selection." All **28 officially supported languages** now ship in a **single ISO**.
* **Multilingual GRUB Boot Menu:** Users can now select their native language directly from the GRUB boot menu before entering the live session. We embedded `unicode.pf2` to ensure proper rendering of CJK, Arabic, and Thai scripts right at the bootloader stage.
* **Smart Installer & Keyboards:** Ubiquity now explicitly filters and displays only our 28 curated languages. Keyboard layouts dynamically adapt to the user's chosen language, dropping the hardcoded US default.
* **Focused Chinese Input Stack:** Using the `dpkg-divert` mechanism, `anduinos-rime` is installed as the selected Chinese input method for `zh_*` users. This avoids installing more than 20 additional legacy input-method packages from the upstream language-selection path.
* **Expanded Locales:** Added Danish, Ukrainian, Indonesian, Finnish, Hindi, and Greek, bringing the total supported locales to 28 across the globe.

### Streamlined Footprint (~2.5GB ISO)

* **Build Quality Enforcement:** The CI pipeline fails the build if packages excluded by AnduinOS distribution policy, such as Snap or telemetry components, are detected in the image.
* **Modern Lightweight Default Apps:** To reduce ISO bloat while maintaining a modern GNOME experience, we swapped legacy apps for their modern lightweight counterparts:
  * Image Viewer: **Loupe** (replaced `shotwell`)
  * Video Player: **Celluloid** (replaced `showtime`, shipping with `ffmpeg` and `yt-dlp` for broad format and streaming support)
  * Task Manager: **Resources** (replaced `gnome-system-monitor`)
  * Email Client: **Geary**
  * Calculator: **gnome-calculator** (replaced `qalculate`)
  * Music Player: **Amberol** (replaced `gnome-music`, `rhythmbox`, and `rhythmbox-plugins`)
  * Text Editor: **vim-tiny** (replaced full `vim`)
* **Developer Tools Unbundled:** `build-essential`, `gdb`, `gcc`, and `git` are no longer pre-installed to save space. They remain easily installable via APT.
* **First-Party Firewall GUI:** Introduced `anduinos-ufwall-gtk`, a native GTK4 interface for managing UFW firewall rules.
* **Refined Extensions:**
  * Replaced the legacy tray-icons extension with `AppIndicator and KStatusNotifierItem Support` for GNOME 45 and later.
  * Disabled `simple-weather-extension` by default out of respect for privacy (easily toggleable in the Extensions app).
  * Removed the legacy `media-controls` extension to resolve system freezes during browser video playback.
* **Updated Font Stack:** Replaced Ubuntu's default font selection with Cascadia Code, Noto Sans/Serif, and Nerd Fonts Symbols. Emoji rendering uses Twemoji COLRv1 with Noto Color Emoji as a fallback.

**Out-of-the-Box Conveniences:**

* **AppImage Support:** Preconfigured `libfuse2t64` and OpenGL libraries allow compatible AppImages to run without additional system packages.
* **Desktop Permissions:** Integrated `policykit-desktop-privileges` for standard desktop actions such as mounting drives and performing routine updates without repeated administrator-password prompts.
* **Desktop Shortcuts:** Custom `deskmon.service` automatically allows executing `.desktop` files in the `~/Desktop` directory.
* **Office & Hardware Ready:** Pre-installed printing (`cups`, `system-config-printer`) and scanning (`sane-airscan`) utilities.

### Refined Desktop Experience & Customization

* **AnduinOS Appearance:** First-party GTK4/Adwaita settings app with full 28-language i18n. Supports taskbar style switching (Windows 11 centered icons vs. Classic left-aligned), taskbar position (bottom/top/left/right), grouping behavior (Vista-style launcher separation), and an About dialog with hamburger menu.
* **GDM Wallpaper and Fluent Theming:** Added a login-screen wallpaper selector with image preview, backed by `pkexec` and `anduinos-gdm-set-wallpaper`. The GDM theme incorporates Fluent CSS and SVG assets, including styled accessibility controls, and is regenerated during relevant package upgrades. AnduinOS also sponsors [@vinceliuice](https://github.com/vinceliuice), the author of the Fluent GTK and Icon themes used by the desktop.
* **Taskbar and Multitasking:** Dash-to-Panel renders a 1 px Fluent-style panel border. The taskbar isolates monitors and workspaces by default for multi-display setups, and layout changes are applied across connected displays.
* **Right-Click Menu Localization:** Dash-to-Panel panel menu (4 items × 22 languages), DING desktop menu (renamed to "AnduinOS Appearance Settings" × 30 languages), and ArcMenu (Pin/Unpin × 35+ languages) — all localized via `.mo` injection.
* **Wallpaper Pack:** 4 new wallpaper pairs (New Mountain, New Bubbles, 11, AnduinOS); default changed to New Bubbles.
* **System Cleanup:** Removed GTK4 Desktop Icons NG and no-overview extensions for a leaner desktop experience.

### Under-the-Hood Polishes

* Fixed a `blur-my-shell` bug to ensure the taskbar is blurred correctly.
* Updated Ubiquity installer screenshots to reflect the new 2.0 design language.
* Bumped `Fluent-icon-theme` to include the latest icons for modern apps like Resources and Celluloid.
* Bumped `alsa-ucm-conf` to v1.2.16.1 for broader audio hardware compatibility.
* Fixed DING `ding.js` crash (`spawnv` + `chmod +x`).

### Standing on the Shoulders of Giants

AnduinOS 2.0.0 is built on work from the broader open-source community, including the Linux kernel, Ubuntu, GNOME, Rust, Cargo, and many other projects. See the [Third-Party Open Source Software Acknowledgements](https://github.com/AiursoftWeb/AnduinOS-2/blob/master/OSS.md) for the upstream projects and licenses represented in the release.
