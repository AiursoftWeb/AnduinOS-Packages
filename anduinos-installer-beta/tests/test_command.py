import sys
import tempfile
import unittest
from pathlib import Path

from installer_core.command import CommandError, CommandRunner


class CommandRunnerTests(unittest.TestCase):
    def test_output_is_logged_before_the_command_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "release"
            messages = []

            def log(message):
                messages.append(message)
                if message == "ready":
                    release.touch()

            script = (
                "import pathlib, sys, time\n"
                "release = pathlib.Path(sys.argv[1])\n"
                "print('ready', flush=True)\n"
                "while not release.exists():\n"
                "    time.sleep(0.01)\n"
                "print('done', flush=True)\n"
            )
            result = CommandRunner(log).run(
                (sys.executable, "-c", script, str(release)), timeout=2
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ready\ndone\n")
        self.assertEqual(messages[-2:], ["ready", "done"])

    def test_stdout_and_stderr_are_streamed_and_captured_separately(self):
        messages = []
        result = CommandRunner(messages.append).run(
            (
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            )
        )

        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")
        self.assertIn("out", messages)
        self.assertIn("err", messages)

    def test_log_output_false_still_captures_output(self):
        messages = []
        result = CommandRunner(messages.append).run(
            (sys.executable, "-c", "print('captured')"),
            log_output=False,
        )

        self.assertEqual(result.stdout, "captured\n")
        self.assertNotIn("captured", messages)

    def test_input_text_is_sent_to_standard_input(self):
        result = CommandRunner(lambda _message: None).run(
            (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
            input_text="secret",
        )

        self.assertEqual(result.stdout, "secret\n")

    def test_explicit_environment_controls_parsed_command_output(self):
        result = CommandRunner(lambda _message: None).run(
            (
                sys.executable,
                "-c",
                "import os; print(os.environ.get('INSTALLER_TEST_LOCALE'))",
            ),
            environment={"INSTALLER_TEST_LOCALE": "C"},
        )

        self.assertEqual(result.stdout, "C\n")

    def test_timeout_raises_command_error(self):
        with self.assertRaisesRegex(CommandError, "timed out"):
            CommandRunner(lambda _message: None).run(
                (sys.executable, "-c", "import time; time.sleep(5)"),
                timeout=0.05,
            )


if __name__ == "__main__":
    unittest.main()
