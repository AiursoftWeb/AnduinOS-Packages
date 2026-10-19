"""User-session operations for the optional coding agent recommendations."""

import glob
import os
import re
import shlex
import shutil
import subprocess


def environment():
    """Find user installs even when the desktop session predates their PATH edits."""
    env = os.environ.copy()
    paths = [os.path.expanduser(path) for path in (
        '~/.local/bin', '~/.opencode/bin', '~/.minimax-code/bin', '~/.kimi-code/bin',
    )]
    nvm_dir = env.get('NVM_DIR', os.path.expanduser('~/.nvm'))
    paths = sorted(glob.glob(os.path.join(nvm_dir, 'versions/node/*/bin')),
                    key=lambda path: tuple(map(int, re.findall(r'\d+', path.split('/')[-2]))),
                    reverse=True) + paths
    env['PATH'] = os.pathsep.join(paths + [env.get('PATH', os.defpath)])
    return env


def executable(command):
    return shutil.which(command, path=environment()['PATH'])


def open_agent(command, arguments=()):
    binary = executable(command)
    if not binary:
        raise FileNotFoundError(command)
    env = environment()
    if executable('ptyxis'):
        terminal = ['ptyxis', '--new-window']
    elif executable('gnome-terminal'):
        # The default also works with AnduinOS' GNOME Console compatibility link.
        terminal = ['gnome-terminal']
    else:
        raise FileNotFoundError('ptyxis / gnome-terminal')
    subprocess.Popen(
        [*terminal, '--working-directory', os.path.expanduser('~'),
         '--', '/usr/bin/env', 'PATH=' + env['PATH'], binary, *arguments],
        env=env, cwd=os.path.expanduser('~'), start_new_session=True,
    )


def install_agent(script, output):
    """Stream both output channels and preserve failures anywhere in a pipeline."""
    command = ['bash', '-o', 'pipefail', '-c', script]
    output('$ ' + shlex.join(command) + '\n')
    with subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1,
        env=environment(), cwd=os.path.expanduser('~'),
    ) as process:
        for line in process.stdout:
            output(line)
        return process.wait()


def recommendations(_, chinese):
    def agent(identifier, name, description, command, script, arguments=()):
        return {
            'id': identifier, 'name': name, 'description': description,
            'is_installed': lambda: executable(command) is not None,
            'open': lambda: open_agent(command, arguments),
            'install': lambda output: install_agent(script, output),
        }

    opencode = 'curl -fsSL https://opencode.ai/install | bash'
    if chinese:
        agents = [
            agent('kimi', 'Kimi Code', _('A terminal coding assistant from Kimi.'),
                  'kimi', 'curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash'),
            agent('deepseek', 'DeepSeek Harness',
                  _('A coding agent with a local web interface.'),
                  'dsh',
                  'set -e\n'
                  'if ! { node -e "if (Number(process.versions.node.split(\'.\')[0]) < 24) '
                  'process.exit(1)" && command -v npm; }; then\n'
                  '  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"\n'
                  '  mkdir -p "$NVM_DIR"\n'
                  '  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/HEAD/install.sh | bash\n'
                  '  . "$NVM_DIR/nvm.sh"\n'
                  '  nvm install node\n'
                  'fi\n'
                  'npm install --global --prefix "$HOME/.local" '
                  '@deepseek-ai/dsh@latest', ('web',)),
            agent('glm', 'GLM · OpenCode',
                  _('Use GLM in OpenCode. Select Z.AI when configuring your model.'),
                  'opencode', opencode),
            agent('minimax', 'MiniMax Code', _('A terminal coding assistant from MiniMax.'),
                  'mcode', 'curl -fsSL https://filecdn.minimax.chat/public/install.sh | bash'),
            agent('qwen', 'Qwen Code', _('A terminal coding assistant from Qwen.'),
                  'qwen', 'curl -fsSL https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/'
                  'installation/install-qwen-standalone.sh | bash'),
        ]
        agents[1]['notice'] = _(
            'If a compatible Node.js and npm are missing, this also installs nvm and the latest Node.js release for your user account.')
        return agents
    return [
        agent('claude', 'Claude Code', _('A terminal coding assistant from Anthropic.'),
              'claude', 'curl -fsSL https://claude.ai/install.sh | bash'),
        agent('codex', 'Codex', _('A terminal coding assistant from OpenAI.'),
              'codex', 'curl -fsSL https://chatgpt.com/codex/install.sh | sh'),
        agent('copilot', 'GitHub Copilot', _('Bring GitHub Copilot to your terminal.'),
              'copilot', 'curl -fsSL https://gh.io/copilot-install | bash'),
        agent('antigravity', 'Antigravity', _('A terminal coding assistant from Google.'),
              'agy', 'curl -fsSL https://antigravity.google/cli/install.sh | bash'),
        agent('opencode', 'OpenCode', _('An open source coding agent for your terminal.'),
              'opencode', opencode),
    ]
