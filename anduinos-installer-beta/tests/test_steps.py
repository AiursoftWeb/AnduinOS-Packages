import unittest

from installer_core.steps import (
    FailurePolicy,
    InstallContext,
    StepRunner,
    StepStatus,
    StepWarning,
)

from helpers import valid_plan
from test_guided_storage_graph import guided_plan
from installer_core.validation import ExecutionPolicy


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

    def test_explicit_step_warning_overrides_fatal_policy(self):
        class OfflineStep(FakeStep):
            def execute(self, _context):
                self.events.append(f"{self.id}:execute")
                raise StepWarning("Skipped because the installer is offline")

        events = []
        result = StepRunner([OfflineStep("drivers", events)]).run(
            self.context()
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.results[0].status, StepStatus.WARNING)
        self.assertIn("offline", result.results[0].message)

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

    def test_guided_policy_emits_stable_boundaries_around_each_execute(self):
        events = []
        step = FakeStep("copy-system", events)
        plan, _inventory = guided_plan()
        context = InstallContext(
            plan,
            events.append,
            execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST,
        )

        result = StepRunner([step]).run(context)

        before = (
            "[anduinos-boundary:guided-step-copy-system:before]"
        )
        after = "[anduinos-boundary:guided-step-copy-system:after]"
        self.assertTrue(result.succeeded)
        self.assertLess(events.index(before), events.index("copy-system:execute"))
        self.assertLess(events.index("copy-system:execute"), events.index(after))
        self.assertLess(events.index(after), events.index("copy-system:verify"))

    def test_failed_guided_execute_has_no_after_boundary(self):
        events = []
        step = FakeStep("configure-system", events, fail_at="execute")
        plan, _inventory = guided_plan()
        context = InstallContext(
            plan,
            events.append,
            execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST,
        )

        result = StepRunner([step]).run(context)

        self.assertFalse(result.succeeded)
        self.assertIn(
            "[anduinos-boundary:guided-step-configure-system:before]",
            events,
        )
        self.assertNotIn(
            "[anduinos-boundary:guided-step-configure-system:after]",
            events,
        )


if __name__ == "__main__":
    unittest.main()
