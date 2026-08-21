"""Small, injectable command boundary for the privileged executor."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence
from queue import Empty, Queue
from threading import Thread
from time import monotonic


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
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise CommandError(f"Could not run {shlex.join(argv)}: {error}") from error

        assert process.stdout is not None
        assert process.stderr is not None
        streams: Queue[tuple[str, str | None]] = Queue()
        captured = {"stdout": [], "stderr": []}

        def read_stream(name: str) -> None:
            stream = process.stdout if name == "stdout" else process.stderr
            try:
                for line in stream:
                    captured[name].append(line)
                    streams.put((name, line))
            finally:
                stream.close()
                streams.put((name, None))

        readers = tuple(
            Thread(target=read_stream, args=(name,), daemon=True)
            for name in ("stdout", "stderr")
        )
        for reader in readers:
            reader.start()

        if process.stdin is not None:
            try:
                process.stdin.write(input_text or "")
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()

        deadline = None if timeout is None else monotonic() + timeout
        finished_streams = 0
        try:
            while finished_streams < len(readers):
                remaining = (
                    None if deadline is None else max(0.0, deadline - monotonic())
                )
                try:
                    _name, line = streams.get(timeout=remaining)
                except Empty as error:
                    raise subprocess.TimeoutExpired(argv, timeout) from error
                if line is None:
                    finished_streams += 1
                elif log_output:
                    self.log(line.rstrip("\r\n"))

            remaining = (
                None if deadline is None else max(0.0, deadline - monotonic())
            )
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            raise CommandError(
                f"Could not run {shlex.join(argv)}: {error}"
            ) from error
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            for reader in readers:
                reader.join()
            raise

        for reader in readers:
            reader.join()
        result = subprocess.CompletedProcess(
            argv,
            returncode,
            "".join(captured["stdout"]),
            "".join(captured["stderr"]),
        )
        if check and result.returncode != 0:
            raise CommandError(
                f"Command exited with {result.returncode}: {shlex.join(argv)}"
            )
        return result
