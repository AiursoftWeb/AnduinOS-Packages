import subprocess


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.required = []
        self.outputs = {}
        self.root_checked = False

    def require_root(self):
        self.root_checked = True

    def require_commands(self, commands):
        self.required.extend(commands)

    def run(self, command, **kwargs):
        command = tuple(command)
        self.commands.append((command, kwargs))
        output = self.outputs.get(command, ("", "", 0))
        return subprocess.CompletedProcess(
            command, output[2], output[0], output[1]
        )

