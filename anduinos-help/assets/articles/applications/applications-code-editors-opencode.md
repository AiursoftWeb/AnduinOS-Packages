# OpenCode

OpenCode is an open-source AI coding agent designed for developers. It features a native terminal-based user interface, multi-session support, and compatibility with over 75 AI models, including Claude, OpenAI, and Gemini. In addition to its command-line interface (CLI) tool, OpenCode is also available as a desktop application and an IDE extension for various platforms.

OpenCode allows developers to utilize their existing subscriptions to paid services, and it also includes free models that can be used locally. It integrates with a wide range of Language Server Protocol (LSP) servers, enabling AI models to interact more effectively with codebases. The tool is particularly suited for power users and teams who require control, auditability, and want to avoid vendor lock-in, as well as for privacy-sensitive environments.

## System install

To install OpenCode on AnduinOS, you can run:

```bash title="Install OpenCode"
wget https://opencode.ai/download/stable/linux-x64-deb -O opencode.deb
sudo apt install ./opencode.deb -y
rm opencode.deb
```

!!! warning "The link above may be outdated"

    The link above may be outdated. Please visit the [official website](https://opencode.ai/) to get the latest version.

!!! warning "Unable to automatically upgrade this application"

    The above command only installs the application. If you run `sudo apt upgrade`, it won't upgrade it automatically. You will need to manually rerun the above command to upgrade.

    This is because the software provider didn't setup a repository for automatic updates. You will need to check the official website for updates.
