import unittest
from dataclasses import dataclass

from installer_core.steps import (
    FailurePolicy,
    InstallContext,
    StepRunner,
    StepStatus,
)

from helpers import valid_plan


@dataclass
class InjectedStep:
    id: str
    events: list[str]
    fail_at: str | None = None
    title: str = "Injected step"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def _event(self, operation):
        self.events.append(f"{self.id}:{operation}")
        if self.fail_at == operation:
            raise RuntimeError(f"injected {self.id} {operation} failure")

    def preflight(self, context):
        self._event("preflight")

    def execute(self, context):
        self._event("execute")

    def verify(self, context):
        self._event("verify")

    def cleanup(self, context):
        self._event("cleanup")


class FailureInjectionMatrixTests(unittest.TestCase):
    def context(self):
        return InstallContext(valid_plan(), lambda _message: None)

    def test_every_preflight_failure_prevents_all_execution(self):
        for failed_index in range(4):
            with self.subTest(failed_index=failed_index):
                events = []
                steps = [
                    InjectedStep(
                        f"step-{index}",
                        events,
                        fail_at="preflight" if index == failed_index else None,
                        destructive=index >= 1,
                    )
                    for index in range(4)
                ]
                result = StepRunner(steps).run(self.context())
                self.assertFalse(result.succeeded)
                self.assertFalse(result.destructive_started)
                self.assertFalse(any(":execute" in event for event in events))

    def test_every_execute_failure_cleans_current_and_completed_steps(self):
        for failed_index in range(4):
            with self.subTest(failed_index=failed_index):
                events = []
                steps = [
                    InjectedStep(
                        f"step-{index}",
                        events,
                        fail_at="execute" if index == failed_index else None,
                        destructive=index == 1,
                    )
                    for index in range(4)
                ]
                result = StepRunner(steps).run(self.context())
                expected_cleanup = [
                    f"step-{failed_index}:cleanup",
                    *(
                        f"step-{index}:cleanup"
                        for index in reversed(range(failed_index))
                    ),
                ]
                actual_cleanup = [
                    event for event in events if event.endswith(":cleanup")
                ]
                self.assertEqual(actual_cleanup, expected_cleanup)
                self.assertEqual(
                    result.destructive_started,
                    failed_index >= 1,
                )
                self.assertEqual(result.results[-1].status, StepStatus.FAILED)

    def test_every_verify_failure_has_the_same_cleanup_guarantee(self):
        for failed_index in range(4):
            with self.subTest(failed_index=failed_index):
                events = []
                steps = [
                    InjectedStep(
                        f"step-{index}",
                        events,
                        fail_at="verify" if index == failed_index else None,
                    )
                    for index in range(4)
                ]
                result = StepRunner(steps).run(self.context())
                actual_cleanup = [
                    event for event in events if event.endswith(":cleanup")
                ]
                self.assertEqual(
                    actual_cleanup,
                    [
                        f"step-{failed_index}:cleanup",
                        *(
                            f"step-{index}:cleanup"
                            for index in reversed(range(failed_index))
                        ),
                    ],
                )
                self.assertFalse(result.succeeded)

