# AnduinOS Packages

AnduinOS APKG package sources.

## Package conflicts

The following AnduinOS packages explicitly replace or conflict with Ubuntu's official packages.

### GNOME Shell extensions

| AnduinOS Package | Ubuntu Package | Our UUID | Ubuntu UUID | Declared Conflicts | Reason |
|---|---|---|---|---|---|
| `gnome-shell-extension-dash-to-panel-anduinos` | `gnome-shell-extension-dash-to-panel` | `dash-to-panel@jderose9.github.com` | `dash-to-panel@jderose9.github.com` | `gnome-shell-extension-dash-to-panel` | Same UUID -- dpkg file conflict |
| `gnome-shell-extension-gtk4-desktop-icons-ng` | `gnome-shell-extension-desktop-icons-ng` | `gtk4-ding@smedius.gitlab.com` | `ding@rastersoft.com` | -- (Replaces only) | Functional duplicate -- our GTK4 port replaces Ubuntu's GTK3 version |
| `gnome-shell-extension-appindicator-anduinos` | `gnome-shell-extension-appindicator` | `appindicatorsupport@rgcjonas.gmail.com` | `ubuntu-appindicators@ubuntu.com` | `gnome-shell-extension-appindicator` | Functional duplicate -- upstream GNOME Extensions version vs Ubuntu's fork |
| `gnome-shell-extension-tiling-assistant` | *(bundled in gnome-shell-ubuntu-extensions)* | `tiling-assistant@leleat-on-github` | `tiling-assistant@ubuntu.com` | -- (handled by meta-package) | Functional duplicate -- upstream version vs Ubuntu's fork |
| `anduinos-gnome-extensions` | `gnome-shell-ubuntu-extensions` | *(meta-package)* | *(meta-package)* | `gnome-shell-ubuntu-extensions` | Meta-package conflict -- both manage the extension set |

### Audio / Firmware

| AnduinOS Package | Ubuntu Package | Declared Conflicts | Reason |
|---|---|---|---|
| `firmware-sof-anduinos` | `firmware-sof-signed` | `firmware-sof-signed` | Newer snapshot of Intel SOF firmware -- Ubuntu's package (Debian-signed) is stale |
| `alsa-ucm-conf-anduinos` | `alsa-ucm-conf` | `alsa-ucm-conf` | Newer snapshot (`1.2.16` vs `1.2.15.3`) -- needed to match newer SOF firmware |

### System

| AnduinOS Package | Ubuntu Package | Declared Conflicts | Reason |
|---|---|---|---|
| `base-files` | `base-files` | -- (epoch `1:` outranks) | AnduinOS branding -- epoch `1:` ensures our version wins |
| `plymouth-anduinos` | `plymouth-theme-spinner` | `plymouth-theme-spinner` | AnduinOS boot splash -- derives upstream spinner assets into clean `themes/anduinos/` namespace, immune to upstream updates |
| `anduinos-software-properties-common` | `software-properties-common` | `software-properties-common` | AnduinOS fork -- patches `add-apt-repository` for PPA compatibility, ships `anduinos.info`/`anduinos.csv` distro templates |
| `anduinos-software-properties-gtk` | `software-properties-gtk` | `software-properties-gtk` | AnduinOS fork -- strips Ubuntu Pro advertisement from Software & Updates GUI (resolute only) |

## Build

Each package is built via the GitLab CI pipeline (`.gitlab-ci.yml`). Packages use the `Aiursoft.Apkg.Sdk` and can be built locally with:

```
apkg publish
```
