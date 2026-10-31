"""Create and launch per-user application-menu entries for installed AI tools."""

import argparse
import os
from pathlib import Path

import agents


def desktop_string(value):
    return (value.replace('\\', '\\\\').replace('\n', '\\n')
            .replace('\r', '\\r').replace('\t', '\\t'))


def exec_argument(value):
    # Desktop Entry quoting has two escaping layers and is not shell quoting.
    value = value.replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return desktop_string('"' + value + '"')


def create_shortcut(agent):
    if not agent['is_installed']():
        raise FileNotFoundError('The agent executable could not be found.')
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not data_home.is_absolute():
        data_home = Path.home() / '.local/share'
    applications = data_home / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).resolve()
    icon = helper.parent.parent / 'resources/icons/agents' / (agent['id'] + '.svg')
    if not icon.is_file():
        icon = Path('/usr/share/anduinos-oobe/icons/agents') / (agent['id'] + '.svg')
    if not icon.is_file():
        raise FileNotFoundError(icon)
    entry = applications / ('anduinos-agent-' + agent['id'] + '.desktop')
    entry.write_text(
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Name=' + desktop_string(agent['name']) + '\n'
        'Exec=/usr/bin/python3 ' + exec_argument(str(helper)) + ' --launch ' + agent['id'] + '\n'
        'Icon=' + desktop_string(str(icon)) + '\n'
        'Terminal=false\n'
        'Categories=Development;\n'
        'Keywords=AI;Agent;Coding;\n'
        'StartupNotify=false\n', encoding='utf-8')
    print('Created application menu entry: ' + str(entry), flush=True)
    return entry


def main():
    tools = {item['id']: item for chinese in (False, True)
             for item in agents.recommendations(lambda text: text, chinese)}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agent', choices=tools)
    parser.add_argument('--launch', action='store_true', help='Open the installed agent in a new terminal.')
    args = parser.parse_args()
    try:
        if args.launch:
            tools[args.agent]['open']()
        else:
            create_shortcut(tools[args.agent])
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
