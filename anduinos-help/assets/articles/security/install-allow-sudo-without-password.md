# Allow sudo without password

Allowing sudo without password is a security risk, but it can be useful in certain situations.

!!! warning "Security Risk"

    Disabling the password requirement for sudo can be a security risk. This may cause some commands running without sudo to have root permissions and potentially break your system.

However, if you prefer to allow sudo without password, you can follow the steps below.

Open the sudoers file with the visudo command:

```bash title="Allow sudo without password"
sudo mkdir -p /etc/sudoers.d
sudo touch /etc/sudoers.d/$USER
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers.d/$USER
```

That's it! You can now run sudo commands without entering your password.

## Suggested Alternative: Use YubiKey

Instead of completely disabling passwords (which is risky), we highly recommend configuring Linux to accept a physical touch on a YubiKey as authentication. This offers **password-less convenience** with **hardware-level security**.

Check out our dedicated guide in the Skills section: 
👉 [Use YubiKey to Authenticate Sudo and Login](../Skills/Secret-Management/Use-Yubikey-For-PAM-Auth.md)
