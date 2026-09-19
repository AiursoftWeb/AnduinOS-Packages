# Use YubiKey To Login

If you want to enhance the security of your Linux desktop login, you can configure your system to use a YubiKey for authentication. This allows you to log in by simply touching your YubiKey, providing both convenience and strong security.

## Recommended graphical setup

AnduinOS 2.0.2 includes **YubiKey Security Center**, which safely enrolls keys for GDM and `sudo`, preserves password recovery, supports multiple users and keys, and validates privileged policy changes. Use it for new configurations instead of editing PAM files manually.

See [YubiKey Security Center](../../Applications/System/YubiKey-Security-Center/YubiKey-Security-Center.md). The commands below remain available for advanced inspection and recovery.

!!! warning "Replacing an old YubiKey?"

    U2F registration (for sudo/login) and SSH key generation (`ssh-keygen -t ecdsa-sk`) use **completely separate slots** on your YubiKey — they are independent registration paths. Replacing your key means you must re-register for both.

First, install the necessary PAM module and register your YubiKey.

```bash title="Step 1: Install libpam-u2f and register your YubiKey"
# Install necessary PAM module
sudo apt update
sudo apt install -y libpam-u2f

# Register YubiKey for U2F authentication
mkdir -p ~/.config/Yubico
if [ -s ~/.config/Yubico/u2f_keys ]; then
    echo "⚠️  Existing U2F registration found. Appending new key..."
    echo "   (To replace all keys, delete ~/.config/Yubico/u2f_keys first)"
fi
echo "👉 Please touch your YubiKey now to register it..."
pamu2fcfg --username="$(whoami)" >> ~/.config/Yubico/u2f_keys

# Verify: lock screen (Super+L) and try unlocking with YubiKey touch
echo "✅ Registration written. Lock your screen (Super+L) to test — touch the key when LED flashes."
```

Then configure GDM (GNOME Display Manager) to use your YubiKey for login authentication.

```bash title="Step 2: Configure GDM PAM for YubiKey"
# Define the target file (Ubuntu/Debian usually uses gdm-password)
TARGET="/etc/pam.d/gdm-password"

# Check if already configured
if grep -q "pam_u2f.so" "$TARGET"; then
    echo "⚠️  GDM PAM is already configured for YubiKey."
    echo "   (If you just registered a new key, you are all set.)"
else
    # Backup original config
    sudo cp "$TARGET" "$TARGET.bak"

    # Insert the auth rule at line 2 (Top priority)
    # This means: If key is touched, login immediately (skip password).
    sudo sed -i '2i auth       sufficient   pam_u2f.so cue' "$TARGET"
    echo "✅ PAM configured. Next time you login or unlock screen, just touch the Key."
fi
```

### What to expect at the lock screen

When you lock your screen or log out, you will see the password prompt as usual. If your YubiKey is plugged in, it will flash its LED — that is your signal to touch it. The **`cue` option does not display any on-screen message in the GDM graphical interface**, so watch for the blinking light on your key. If you do not touch the key within a few seconds, GDM falls back to the password prompt automatically.

!!! tip "Troubleshooting"

    If the YubiKey does not flash when you lock the screen, verify your key registration and PAM configuration by running the cross-check command in the [Query which keys are trusted](#query-which-keys-are-trusted-by-your-system) section below.

If anything goes wrong, press `Ctrl + Alt + F3` to switch to a terminal, log in with your username and password, and restore the original PAM configuration:

```bash title="Restore GDM PAM Configuration"
sudo mv /etc/pam.d/gdm-password.bak /etc/pam.d/gdm-password
```

This will revert the changes and allow you to log in with your password again.

## Use Yubikey to authenticate sudo commands

If you want to use your YubiKey for authenticating `sudo` commands (giving you passwordless convenience with hardware security), you can modify the PAM configuration for `sudo`.

!!! danger "NOPASSWD conflicts with YubiKey PAM"

    If you have `NOPASSWD:ALL` set in `/etc/sudoers.d/`, sudo **completely skips** the PAM authentication stack — your YubiKey will never be asked for. The two settings are mutually exclusive. Ensure you do not have NOPASSWD enabled if you want to use YubiKey.

Run this script to configure sudo to accept your registered YubiKey:

```bash title="Step 3: Configure sudo PAM for YubiKey"
# Check if already configured
if grep -q "pam_u2f.so" /etc/pam.d/sudo; then
    echo "⚠️  Sudo PAM is already configured for YubiKey."
else
    # Backup original config
    sudo cp /etc/pam.d/sudo /etc/pam.d/sudo.bak

    # Insert the auth rule at line 2 (right after include common-auth)
    sudo sed -i '2i auth       sufficient   pam_u2f.so cue' /etc/pam.d/sudo
    echo "✅ PAM configured."
fi

# Test: clear sudo cache and verify YubiKey is required
echo ""
echo "👉 Testing — touch your YubiKey when LED flashes..."
sudo -k
sudo ls 2>&1 && echo "✅ YubiKey sudo authentication works!" || echo "❌ Test failed"
```

## Query which keys are trusted by your system

After setting up YubiKey authentication, you may want to verify which keys your system actually trusts. The following commands help you inspect the current state across both U2F (sudo/login) and SSH.

### List trusted U2F keys (sudo + GDM login)

U2F registrations for PAM-based authentication are stored in `~/.config/Yubico/u2f_keys`.

```bash title="View registered U2F key handles"
echo "=== Trusted U2F keys (sudo + GDM login) ==="
cat ~/.config/Yubico/u2f_keys 2>/dev/null | awk -F: '{print $2}' | tr ',' '\n' | grep -v 'es256\|presence'
```

### List trusted SSH keys

SSH keys stored on your YubiKey use a different slot and are managed through the SSH agent.

```bash title="View SSH keys in agent"
echo "=== Trusted SSH keys ==="
ssh-add -L 2>/dev/null | grep "sk-"
```

### Check which YubiKey is currently inserted

```bash title="List connected YubiKeys"
ykman list 2>/dev/null
```

### Cross-check: is the connected key trusted?

The most reliable way to verify your YubiKey is registered correctly is to test it directly:

- **GDM login:** Lock your screen (`Super+L`). If the YubiKey LED flashes, touch it — you should be logged in.
- **sudo:** Run `sudo -k` (clears cache), then `sudo ls`. The YubiKey LED should flash — touch to authenticate.

```bash title="Quick sudo test"
sudo -k && sudo ls
# Touch YubiKey when LED flashes
```
