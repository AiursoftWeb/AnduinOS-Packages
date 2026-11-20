# Update your system

Keeping your system up-to-date helps you receive security patches, bug fixes, and new features. AnduinOS combines background updates with manual update options; periodically check for updates even when automatic updates are enabled.

## Automatic Updates

The `unattended-upgrades` background service can install eligible updates without requiring you to start each upgrade. Eligibility depends on the installed policy, enabled repositories, package dependencies, and any administrator overrides. An available update is not a guarantee that it has already been installed.

### Repository Policy in the 2.0.3 Development Update

The policy below is shipped by `anduinos-apt-config` or `anduinos-apt-config-dev` starting with package revision `2.0.2-5` (plus its distribution suffix, such as `+resolute`). It is part of the [AnduinOS 2.0.3 development changes](../Release-Notes/2.0.3.md); availability depends on publication to your configured repository.

| Package source | Production configuration | Testing configuration |
| --- | --- | --- |
| Ubuntu base and `-security` archives for the current release | Allowed | Allowed |
| `packages.anduinos.com`, current release's `-addon` and `-webapps` channels | Allowed | Not added |
| `apkg-dev.aiursoft.com`, current release's `-addon` and `-webapps` channels | Not added | Allowed |
| Ubuntu `-updates`, `-backports`, and `-proposed` archives | Not added | Not added |
| Unrelated third-party repositories | Not added | Not added |

The production package is `anduinos-apt-config`; the testing package is `anduinos-apt-config-dev`. The AnduinOS rules require the corresponding hostname, `Origin: Aiursoft Apkg`, and the current release channel to match together. They do not bypass APT's repository-signature checks. Testing-source users receive testing packages, which may be less stable than production packages.

AnduinOS repository updates include application and system-integration fixes and features, not only security patches. Merely adding a third-party APT source does not add it to this default unattended-update policy. However, these defaults are additive: they do not remove administrator-defined allowances in other APT configuration files.

!!! important "Existing systems need the new configuration first"

    The earlier `2.0.2-4` policy added Ubuntu base and security archives, but did not add AnduinOS's own repositories. Do not rely on that policy to install its own replacement automatically. Once the new configuration package is available, use App Store or the manual APT update commands below to update the configuration package you already use. You do not need to switch between production and testing sources.

You can inspect installed configuration-package versions with:

```bash title="Check installed APT policy packages"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' 'anduinos-apt-config*'
```

Updates to kernels or running applications may still require a restart or logout to take full effect. Automatic APT updates do not replace checking for updates to Flatpak applications or other software managed separately.

## Managing Updates via App Store (Recommended)

For reviewing available system and application updates, use the built-in **App Store** (GNOME Software).

1. Open your application menu and launch **App Store**.
2. Navigate to the **Updates** tab.
3. Click **Download** if updates are available.
4. Once downloaded, click **Restart & Update**.

![App Store Updates Tab](images/app-store-updates.png)

### Why "Restart & Update"? (Offline Updates)
You might wonder why the system needs to restart to apply updates. AnduinOS uses a modern mechanism called **Offline Updates**. 

![Restart & Install Prompt](images/app-store-restart.png)

Updating system components during a desktop session can leave running applications using older code while newly started processes use the updated versions. Some updates require applications to restart or the system to reboot.
When you click "Restart & Update", the system applies the updates outside the normal desktop session and then restarts. This reduces interference from running applications, but does not eliminate all update risks or replace backups.

## (Alternative) Command Line Updates

If you are an advanced user or managing a server without a graphical interface, you can update your system using the standard `apt` package manager.

These commands are not restricted by the unattended-update source list. They consider eligible packages from all enabled APT repositories, including AnduinOS and third-party sources, subject to normal APT pinning and dependency rules.

```bash title="Update your package list and upgrade"
sudo apt update
sudo apt upgrade
```

*Note: While `apt upgrade` is fast, be aware that updating graphical components while you are actively using them may cause temporary visual glitches or require you to log out and log back in to fully apply the changes.*
