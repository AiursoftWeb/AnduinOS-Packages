# Manage SSH Keys

SSH Keys are a secure way to authenticate to a server. They are a pair of cryptographic keys that can be used to authenticate to an SSH server as an alternative to password-based logins. One key is private and the other is public. When you generate an SSH key pair, you will get a private key and a public key. The private key is kept on the computer you log in from, while the public key is stored in `~/.ssh/authorized_keys` on each server you want to access.

If the destination is an AnduinOS desktop, [enable its SSH listener](../../Install/Enable-SSH.md) before copying the key.

## Generate SSH Key Pair

To generate a modern Ed25519 SSH key pair, use `ssh-keygen`:

```bash
ssh-keygen -t ed25519
```

After running the command, you will be prompted to enter a file in which to save the key. Press Enter to use the default location (`~/.ssh/id_ed25519`). You will also be prompted for a passphrase. A passphrase protects the private key if the file is copied or stolen and is strongly recommended.

The generated `id_ed25519` file is private and must not be shared. The `id_ed25519.pub` file is public and can be copied to servers and Git hosting providers.

## Copy SSH Key to Server

To authorize your public key on a server, use `ssh-copy-id`:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@hostname
```

Replace `user` with your username and `hostname` with the IP address or domain name of the server you want to copy the key to. You will be prompted to enter your password for the server. Once the key is copied, the server no longer needs the account password for that key; your local private-key passphrase may still be requested.

After that, you can log in to the server using the following command:

```bash
ssh user@hostname
```

## Add SSH Key to Git Server

Also, you can use SSH key to authenticate git servers like GitHub, GitLab, Bitbucket, etc. by adding your public key to your account settings.

To add your SSH key to your GitHub account, you can follow these steps:

- Copy your public key to the clipboard.

```bash
xclip -selection clipboard < ~/.ssh/id_ed25519.pub
```

- Go to your GitHub account settings.
- Click on "SSH and GPG keys" in the left sidebar.
- Click on "New SSH key".
- Paste your public key into the "Key" field.
- Click on "Add SSH key".
- Confirm the action by entering your GitHub password.
- You can now use SSH to authenticate to GitHub.

To make sure your SSH key is being used, you can test the connection to the server.

```bash
ssh git@github.com
```

## Backup SSH Keys

It is important to back up your SSH keys to prevent data loss. Copy the `~/.ssh` directory only to an encrypted backup or another location that protects private-key confidentiality.

```bash
cp -a ~/.ssh /path/to/encrypted-backup/
```

Make sure to keep the backup secure and up-to-date.

## Restore SSH Keys

If you need to restore your SSH keys from a backup, you can copy the `~/.ssh` directory back to your home directory.

```bash
install -d -m 700 ~/.ssh
cp -a /path/to/encrypted-backup/.ssh/. ~/.ssh/
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

Make sure to set the correct permissions on the private key file.

## SSH to Server

To SSH to a server using a specific private key, you can use the `-i` option.

```bash
ssh -i /path/to/private_key user@hostname
```

Replace `/path/to/private_key` with the path to your private key file, `user` with your username, and `hostname` with the IP address or domain name of the server.

### Via SSH gateway

In some cases, the Server might behind a firewall or NAT, and you need to use a jump host to connect to it. You can use the `-J` option to specify a jump host.

```bash
ssh -J user@jump_host user@hostname
```

Replace `user@jump_host` with the username and hostname of the jump host, and `user@hostname` with the username and hostname of the server.

### Via HTTP proxy

In some cases, you might need to connect to a server through an HTTP proxy. You can use the `ProxyCommand` option to specify the proxy command.

```bash
ssh -o "ProxyCommand=nc -X connect -x <proxy_host>:<proxy_port> %h %p" <user>@<host>
```

Replace `<proxy_host>` and `<proxy_port>` with the hostname and port of the proxy server, `<user>` with your username, and `<host>` with the IP address or domain name of the server.

## Renew SSH Keys

This guide walks you through securely rotating your SSH key pair across remote servers.

### 1. Generate a New SSH Key Pair

```bash title="Generate a new SSH key pair with ed25519 algorithm"
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_new -C "renewed-key"
```

> `ed25519` is fast, compact, and secure. Use a meaningful comment for traceability.

### 2. Deploy the New Public Key to Servers

Run for each server:

```bash title="Copy the new public key to the remote server"
ssh-copy-id -i ~/.ssh/id_ed25519_new.pub user@server_ip
```

Or manually:

```bash title="Manually append the new public key to authorized_keys"
cat ~/.ssh/id_ed25519_new.pub | ssh user@server_ip 'cat >> ~/.ssh/authorized_keys'
```

### 3. Verify New Key Works

```bash title="Test the new SSH key"
ssh -i ~/.ssh/id_ed25519_new user@server_ip
```

If login succeeds, keep this session open until the new key has also worked in a separate connection. Do not remove the old key yet.

### 4. Select the New Key by Default (Optional)

Add the new identity to the relevant host entry in `~/.ssh/config` instead of renaming an Ed25519 key to an RSA filename:

```text title="~/.ssh/config"
Host server.example.com
    User your-username
    IdentityFile ~/.ssh/id_ed25519_new
    IdentitiesOnly yes
```

### 5. Remove Old Keys from Remote Servers

Keep the verified new-key session open, connect to the server in a second terminal, and back up the authorization file:

```bash title="Back Up Authorized Keys on the Server"
cp -a ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak
```

Edit `~/.ssh/authorized_keys` on the server and remove only the exact line containing the old public key. Do not replace the file with its first or last line: it may contain independent recovery or administrator keys.

### 6. Test Final Login

```bash title="Final test to ensure the new key works"
ssh -i ~/.ssh/id_ed25519_new user@server_ip
```

If it works, your new key is fully in use.

### 7. Clean Up (Optional)

Move the old private key to a secure offline archive. Delete it only after the new key has worked in a separate session and every required server has been updated.
