# Antigravity

!!! tip "AnduinOS Verified App"

    Antigravity is an AnduinOS verified app and it runs awesome on AnduinOS, with easy installation.

Google Antigravity is an agentic development platform, evolving the IDE into the agent-first era. Antigravity enables developers to operate at a higher, task-oriented level by managing agents across workspaces, while retaining a familiar AI IDE experience at its core. Agents operate across the editor, terminal, and browser, enabling them to autonomously plan and execute complex, end-to-end tasks elevating all aspects of software development.

## Install Antigravity CLI

To install the Antigravity CLI on AnduinOS, you can run:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

## Install Antigravity IDE

To install the Antigravity IDE (Hub) on AnduinOS, you can run the following commands:

```bash
# Download and extract the tarball.
url="https://storage.googleapis.com/antigravity-public/antigravity-hub/2.1.4-6481382726303744/linux-x64/Antigravity.tar.gz"
wget -O /tmp/antigravity.tar.gz "$url"
sudo rm -rf /opt/Antigravity-x64-old-backup
sudo mv /opt/Antigravity-x64 /opt/Antigravity-x64-old-backup || true
sudo tar -xzf /tmp/antigravity.tar.gz -C /opt
sudo chown -R "$USER":"$USER" /opt/Antigravity-x64
rm /tmp/antigravity.tar.gz

# Create a launcher.
cat << EOF | sudo tee /usr/share/applications/antigravity.desktop >/dev/null
[Desktop Entry]
Name=Antigravity
Comment=Antigravity Application
Exec=/opt/Antigravity-x64/antigravity --no-sandbox
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
EOF
```

!!! warning "The link above may be outdated"

    The link above may be outdated. Please visit the [official download page](https://antigravity.google/download) to get the latest version.

!!! warning "Sandbox note for Ubuntu 24.04+"

    The `--no-sandbox` flag is required on Ubuntu 24.04+ due to AppArmor restrictions.

!!! warning "Unable to automatically upgrade this application"

    The above command only installs the launcher. If you run `sudo apt upgrade`, it won't upgrade it automatically. You will need to manually rerun the above command to upgrade.

    This is because the software provider didn't setup a repository for automatic updates. You will need to check the official website for updates.
