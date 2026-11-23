# Codex CLI

!!! tip "AnduinOS Verified App - Open Source"

    [Codex CLI](https://github.com/openai/codex) is an AnduinOS verified app and it runs awesome on AnduinOS.

Codex CLI is a lightweight coding agent from OpenAI that runs locally in your terminal. It brings the power of OpenAI's AI models directly into your command-line workflow, helping you write, edit, and understand code.

To install Codex CLI on AnduinOS, you can run:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

The script above downloads and installs the standalone Codex CLI binary. After installation, you can start using it by running the `codex` command in your terminal.

That's it! You now have Codex CLI installed on your AnduinOS system.

!!! warning "Unable to automatically upgrade this application"

    The above command only installs the application. If you run `sudo apt upgrade`, it won't upgrade it automatically. You will need to manually rerun the above command to upgrade.

    This is because the software provider didn't setup a repository for automatic updates. You will need to check the official website for updates.
