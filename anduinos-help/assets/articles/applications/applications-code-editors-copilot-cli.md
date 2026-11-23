# Copilot CLI

!!! tip "AnduinOS Verified App - Open Source"

    [Copilot CLI](https://github.com/github/copilot-cli) is an AnduinOS verified app and it runs awesome on AnduinOS.

Copilot CLI is a command-line interface tool for interacting with GitHub Copilot AI. It brings the power of GitHub Copilot directly into your terminal, helping you with code suggestions, command explanations, and shell command generation.

To install Copilot CLI on AnduinOS, you can run:

```bash
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg --yes
NODE_MAJOR=24
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt update
sudo apt install nodejs
sudo npm install -g @github/copilot
```

The script above installs Node.js (with npm) as a prerequisite for Copilot CLI. If you already have Node.js installed, you can skip the Node.js installation part.

That's it! You now have Copilot CLI installed on your AnduinOS system. You can start using it by running the `copilot` command in your terminal.

!!! warning "Unable to automatically upgrade this application"

    The above command only installs the application. If you run `sudo apt upgrade`, it won't upgrade it automatically. You will need to manually rerun the above command to upgrade.

    This is because the software provider didn't setup a repository for automatic updates. You will need to check the official website for updates.
