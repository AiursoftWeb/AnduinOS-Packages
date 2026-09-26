# YubiKey Security Center

YubiKey Security Center connects a supported FIDO security key to AnduinOS sign-in, `sudo`, SSH authentication, Git commit signing, and passkey workflows.

Open the application menu, search for **Security Center**, and insert the YubiKey. The home page shows the connected keys and the protection enabled for the current user.

![YubiKey Security Center home page](images/security-center-home.png)

The application does not store your FIDO PIN or copy private SSH credentials from the YubiKey. Administrator authentication is requested only when a system authentication policy must be changed.

!!! important "Keep a recovery method"

    Do not make one physical key your only way to access an important computer or account. Keep the account password enabled, enroll a second security key where possible, and preserve the recovery methods supplied by online services.

## Unlock the desktop with a YubiKey

Open **Unlock GDM** to choose which connected keys may sign in or unlock the current user's desktop.

![Allow a YubiKey to unlock GDM](images/unlock-gdm.png)

Turn on the switch beside the intended key. During enrollment:

1. Disconnect other security keys.
2. Start enrollment for the remaining key.
3. Touch the key when it flashes.
4. Test the result by locking the desktop with `Super+L`.

The normal account password remains available. YubiKey authentication is an additional sign-in method, not a replacement that removes password recovery.

To enroll another key, repeat the process with only that key connected. Removing one enrollment does not remove other users or other enrolled keys.

## Authorize sudo with a YubiKey

Open **Unlock sudo** to allow an enrolled YubiKey to authorize administrator commands for the current user.

![Allow a YubiKey to authorize sudo](images/unlock-sudo.png)

Turn on the switch beside the key, then test it in a terminal:

```bash title="Test YubiKey sudo authentication"
sudo -k
sudo true
```

Touch the YubiKey when it flashes. If the key is unavailable, the account password remains a valid authentication path.

### Passwordless sudo and YubiKey authentication

The **Allow sudo without authentication** option is different from YubiKey authorization. Passwordless sudo skips authentication completely, so neither a password nor a YubiKey will be requested.

Keep passwordless sudo disabled when you want the YubiKey to protect administrator access. Security Center will not disable an existing passwordless policy until at least one working sudo credential has been enrolled, which reduces the risk of locking the user out during the transition.

## Use resident SSH keys

A resident SSH credential keeps its private key inside the YubiKey. Security Center can inspect compatible credentials, load them into the desktop SSH agent, create a new credential on a selected key, copy its public key, and test that the key can sign.

![Hardware-backed SSH keys](images/ssh-keys.png)

Inspection requires the YubiKey's FIDO PIN and may require a touch. The PIN is used only for that operation and is not placed in command arguments, logs, or files.

After a key is loaded, copy its public key and add it to the remote server's `~/.ssh/authorized_keys` or to the SSH-key page provided by GitHub, GitLab, or another service. The public key and its SHA-256 fingerprint may be shared; the private credential remains non-exportable on the YubiKey.

!!! warning "A resident private key cannot be backed up"

    Create a separate credential on a second YubiKey and authorize both public keys on important servers. If the only key is lost or damaged, its private credential cannot be recovered.

### Reuse established SSH connections

The **Reuse SSH connections in the background** switch adds a managed OpenSSH client configuration for the current user. After the first YubiKey authentication, an idle connection may be reused for up to ten minutes.

This setting does not weaken the YubiKey's touch policy, load a key, or connect to a server by itself. It uses `ControlMaster auto` and `ControlPersist 10m`; earlier host-specific settings in the user's SSH configuration continue to take priority. Turning the switch off does not terminate an already-running connection.

## Sign Git commits

Open **Git signing**, load or inspect the resident keys, and select the key that should sign new commits.

![Configure Git commit signing](images/git-signing.png)

Security Center configures Git to use SSH-format signatures for the current user. The choice takes effect immediately. Select **No signing** to stop signing new commits and tags by default.

Use **Test signing** to create and verify a temporary SSH signature. The test does not create a repository, commit, tag, or branch. Use **Copy public key** when registering the key with a hosting service.

On GitHub, the same public key must be added as a **Signing Key** even if it was already added as an **Authentication Key** for Git over SSH. Other hosting services may use different names for this setting.

## Use passkeys

The **Passkeys** page explains how to create and use passkeys stored on a compatible YubiKey.

![Passkey guidance](images/passkeys.png)

Passkey registration starts on the website or in the application where the account exists:

1. Choose to create a passkey on the website or application.
2. Select the security key when prompted.
3. Enter the FIDO PIN and touch the YubiKey.
4. Keep another sign-in or recovery method for the account.

Security Center does not inspect, create, or delete passkeys. When available, it can open Yubico Authenticator for credential management; otherwise it can open the official Flathub installation page in Software.

## Removing or replacing a key

Desktop/sudo enrollment, resident SSH credentials, and website passkeys are independent. Removing a key from one Security Center page does not automatically revoke it everywhere else.

When replacing a YubiKey:

1. Enroll the new key for **Unlock GDM** and **Unlock sudo**.
2. Create or load a resident SSH credential and authorize its public key on every server.
3. Register the new public key separately for Git commit signing.
4. Add a new passkey to each online account.
5. Test the new key before removing the old registrations.

For advanced command-line procedures, see [Use YubiKey to Login](../../../Skills/Secret-Management/Use-Yubikey-For-PAM-Auth.md) and [Manage SSH Keys with YubiKey](../../../Skills/Secret-Management/Manage-SSH-Keys-with-Yubikey.md).
