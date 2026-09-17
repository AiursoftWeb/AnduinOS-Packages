# Qwen Code

!!! tip "AnduinOS Verified App - Open Source"

    [Qwen Code](https://github.com/QwenLM/qwen-code) is an AnduinOS verified app and it runs awesome on AnduinOS.

Qwen Code is an open-source AI agent for the terminal, optimized for Qwen series models. It helps you understand large codebases, automate tedious work, and ship faster.

## Features

- **Multi-protocol, OAuth free tier**: Use OpenAI / Anthropic / Gemini-compatible APIs, or sign in with Qwen OAuth.
- **Open-source, co-evolving**: Both the framework and the Qwen3-Coder model are open-source.
- **Agentic workflow, feature-rich**: Rich built-in tools (Skills, SubAgents) for a full agentic workflow and a Claude Code-like experience.
- **Terminal-first, IDE-friendly**: Built for developers who live in the command line, with optional integration for VS Code, Zed, and JetBrains IDEs.

## Installation

### Quick Install (Recommended)

To install Qwen Code on AnduinOS, you can run:

```bash
bash -c "$(curl -fsSL https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen.sh)"
```

### Manual Installation

Make sure you have Node.js 20 or later installed, then run:

```bash
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg --yes
NODE_MAJOR=24
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt update
sudo apt install nodejs
sudo npm install -g @qwen-code/qwen-code@latest
```

## Authentication

Qwen Code supports two authentication methods:

- **Qwen OAuth (recommended & free)**: sign in with your qwen.ai account in a browser. Start `qwen`, then run `/auth`.
- **API-KEY**: use an API key to connect to any supported provider (OpenAI, Anthropic, Google GenAI, Alibaba Cloud ModelStudio, and other compatible endpoints).

!!! note "Headless Environments"

    In non-interactive or headless environments (e.g., CI, SSH, containers), you typically cannot complete the OAuth browser login flow. In these cases, please use the API-KEY authentication method.

## Usage

### Interactive Mode

Run `qwen` in your project folder to launch the interactive terminal UI:

```bash
qwen
```

### Headless Mode

Use `-p` to run Qwen Code without the interactive UI—ideal for scripts, automation, and CI/CD:

```bash
qwen -p "your question"
```

## Commands & Shortcuts

- `/help` - Display available commands
- `/auth` - Switch authentication methods
- `/clear` - Clear conversation history
- `/exit` or `/quit` - Exit Qwen Code
- `Ctrl+C` - Cancel current operation
- `Ctrl+D` - Exit (on empty line)

For more information, please visit the [Qwen Code website](https://github.com/QwenLM/qwen-code).
