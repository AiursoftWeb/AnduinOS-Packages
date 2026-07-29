"""Small, injectable command boundary for the privileged executor."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence


class CommandError(RuntimeError):
    pass


class CommandRunner:
    def __init__(self, log: Callable[[str], None]):
        self.log = log

    def require_root(self) -> None:
        if os.geteuid() != 0:
            raise CommandError("The installation executor must run as root")

    def require_commands(self, commands: Sequence[str]) -> None:
        missing = sorted(command for command in commands if not shutil.which(command))
        if missing:
            raise CommandError(
                "Required commands are missing: " + ", ".join(missing)
            )

    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: int | None = None,
        check: bool = True,
        log_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(value) for value in command]
        self.log(f"$ {shlex.join(argv)}")
        try:
            result = subprocess.run(
                argv,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CommandError(f"Could not run {shlex.join(argv)}: {error}") from error
        if log_output and result.stdout:
            self.log(result.stdout.rstrip())
        if log_output and result.stderr:
            self.log(result.stderr.rstrip())
        if check and result.returncode != 0:
            raise CommandError(
                f"Command exited with {result.returncode}: {shlex.join(argv)}"
            )
        return result
