import unittest

from installer_core.steps import (
    FailurePolicy,
    InstallContext,
    StepRunner,
    StepStatus,
)

from helpers import valid_plan


class FakeStep:
    def __init__(
        self,
        step_id,
        events,
        *,
        policy=FailurePolicy.FATAL,
        fail_at=None,
        destructive=False,
    ):
        self.id = step_id
        self.title = step_id
        self.failure_policy = policy
        self.progress_weight = 1
        self.destructive = destructive
        self.events = events
        self.fail_at = fail_at

    def _event(self, name):
        self.events.append(f"{self.id}:{name}")
        if self.fail_at == name:
            raise RuntimeError(f"{self.id} {name} failed")

    def preflight(self, _context):
        self._event("preflight")

    def execute(self, _context):
        self._event("execute")

    def verify(self, _context):
        self._event("verify")

    def cleanup(self, _context):
        self._event("cleanup")


class StepRunnerTests(unittest.TestCase):
    def context(self):
        return InstallContext(valid_plan(), lambda _message: None)

    def test_all_preflight_runs_before_execution(self):
        events = []
        steps = [FakeStep("one", events), FakeStep("two", events)]
        result = StepRunner(steps).run(self.context())
        self.assertTrue(result.succeeded)
        self.assertEqual(
            events[:2], ["one:preflight", "two:preflight"]
        )

    def test_preflight_failure_never_starts_destructive_work(self):
        events = []
        logs = []
        statuses = []
        steps = [
            FakeStep("erase", events, destructive=True),
            FakeStep("check", events, fail_at="preflight"),
        ]
        context = InstallContext(valid_plan(), logs.append)
        result = StepRunner(
            steps,
            status=lambda step, status, message: statuses.append(
                (step, status, message)
            ),
        ).run(context)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.destructive_started)
        self.assertNotIn("erase:execute", events)
        self.assertIn("[preflight:erase] erase", logs)
        self.assertIn("[preflight:check] check", logs)
        self.assertEqual(
            result.results[0].message,
            "Preflight failed for check: check preflight failed",
        )
        self.assertEqual(statuses[-1], (
            "check",
            StepStatus.FAILED,
            "Preflight failed for check: check preflight failed",
        ))

    def test_fatal_failure_cleans_completed_steps_in_reverse(self):
        events = []
        steps = [
            FakeStep("mount", events),
            FakeStep("copy", events, fail_at="execute"),
        ]
        result = StepRunner(steps).run(self.context())
        self.assertFalse(result.succeeded)
        self.assertEqual(events[-1], "mount:cleanup")
        self.assertIn("copy:cleanup", events)
        self.assertLess(
            events.index("copy:cleanup"), events.index("mount:cleanup")
        )

    def test_warning_does_not_make_install_fail(self):
        events = []
        steps = [
            FakeStep(
                "optional",
                events,
                policy=FailurePolicy.WARNING,
                fail_at="execute",
            )
        ]
        result = StepRunner(steps).run(self.context())
        self.assertTrue(result.succeeded)
        self.assertEqual(result.results[0].status, StepStatus.WARNING)

    def test_emits_running_and_terminal_status_for_each_step(self):
        events = []
        statuses = []
        steps = [
            FakeStep("ok", events),
            FakeStep(
                "optional",
                events,
                policy=FailurePolicy.WARNING,
                fail_at="execute",
            ),
        ]
        result = StepRunner(
            steps,
            status=lambda step, status, message: statuses.append(
                (step, status, message)
            ),
        ).run(self.context())
        self.assertTrue(result.succeeded)
        self.assertEqual(
            [(step, status) for step, status, _message in statuses],
            [
                ("ok", StepStatus.RUNNING),
                ("ok", StepStatus.SUCCEEDED),
                ("optional", StepStatus.RUNNING),
                ("optional", StepStatus.WARNING),
            ],
        )
        self.assertIn("failed", statuses[-1][2])


if __name__ == "__main__":
    unittest.main()
