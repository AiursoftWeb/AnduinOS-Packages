# Enable SSH

SSH provides remote terminal access to your computer. New installations of AnduinOS 2.0.2 and later include the OpenSSH server so that GNOME Settings can manage this feature, but SSH remains disabled by default and the computer does not listen on port 22 until you enable it.

!!! warning "Remote Access"

    Enabling SSH exposes a login service to the network. Use a strong account password, prefer SSH keys, and allow access only from networks you trust.

## Enable SSH in GNOME Settings

1. Open **Settings**.
2. Select **System** and then **Secure Shell**.
3. Turn on **Secure Shell** and approve the authentication prompt if one appears.

![Secure Shell enabled in GNOME Settings](images/gnome-settings-secure-shell.png)

This switch manages the system SSH listener. Turning it off prevents new incoming SSH connections without uninstalling OpenSSH, terminating established sessions, or deleting the computer's SSH host keys.

### Upgraded Systems

AnduinOS does not automatically install a new network server on systems upgraded from an earlier release. If **Secure Shell** is unavailable or cannot be enabled, install the server once:

```bash title="Install the OpenSSH Server"
sudo apt update
sudo apt install openssh-server
```

Systems that already had OpenSSH installed keep their existing enabled or disabled state during an AnduinOS upgrade.

## Enable SSH During Installation

The AnduinOS installer includes **Allow SSH login with the account password** under **Advanced Options**. Enabling it starts the SSH listener when the installed system boots and permits the new user's non-empty account password for SSH authentication. It does not permit empty passwords or direct root login.

Leaving the option off keeps SSH disabled. On a new AnduinOS 2.0.2 installation, you can still enable it later with GNOME Settings without installing another package.

## Command-Line Control

To enable the same socket-activated SSH listener managed by GNOME Settings, run:

```bash title="Enable SSH"
sudo systemctl enable --now ssh.socket
```

To stop SSH and keep it disabled across reboots, run:

```bash title="Disable SSH"
sudo systemctl disable --now ssh.socket ssh.service
```

Disabling both units also covers systems on which an administrator previously enabled the traditional `ssh.service` directly.

## Connect from Another Computer

Find the AnduinOS computer's local IP addresses:

```bash title="Show IP Addresses"
hostname -I
```

Then connect from another computer, replacing the example values with your AnduinOS username and address:

```bash title="Connect to AnduinOS"
ssh username@192.168.1.100
```

For stronger authentication than an account password, follow the [Manage SSH Keys](../Skills/Secret-Management/Manage-SSH-Keys.md) guide.

## Firewall Access

GNOME Settings controls the SSH listener, but it does not configure an external router or network firewall. If UFW is enabled, make sure its OpenSSH rule is present before connecting:

```bash title="Allow SSH Through UFW"
sudo ufw allow OpenSSH
sudo ufw status
```

The AnduinOS 2.0.2 installer prepares this rule on new installations. Running the command again is safe and is useful for upgraded or manually configured systems. See the [Firewall Guide](./Enable-Firewall.md) for graphical configuration.

## Check the SSH Listener

If a connection is refused, check both the socket state and the listening port:

```bash title="Check SSH Status"
systemctl status ssh.socket
sudo ss -ltnp | grep ':22'
```

If the socket is active and port 22 is listening, check UFW, the client and server addresses, and any router or virtual-machine networking rules between the two computers.
