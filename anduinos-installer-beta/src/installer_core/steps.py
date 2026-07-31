"""Explicit installation step state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from .model import InstallPlan


class FailurePolicy(str, Enum):
    FATAL = "fatal"
    WARNING = "warning"
    BEST_EFFORT = "best-effort"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepWarning(RuntimeError):
    """Skip the current action with a visible warning, regardless of policy."""


@dataclass
class InstallContext:
    plan: InstallPlan
    log: Callable[[str], None]
    values: dict[str, Any] = field(default_factory=dict)
    destructive_started: bool = False


class InstallStep(Protocol):
    id: str
    title: str
    failure_policy: FailurePolicy
    progress_weight: int
    destructive: bool

    def preflight(self, context: InstallContext) -> None: ...

    def execute(self, context: InstallContext) -> None: ...

    def verify(self, context: InstallContext) -> None: ...

    def cleanup(self, context: InstallContext) -> None: ...


@dataclass(frozen=True)
class StepResult:
    step_id: str
    status: StepStatus
    message: str = ""


@dataclass(frozen=True)
class InstallResult:
    succeeded: bool
    results: tuple[StepResult, ...]
    destructive_started: bool

    @property
    def warnings(self) -> tuple[StepResult, ...]:
        return tuple(r for r in self.results if r.status is StepStatus.WARNING)


class StepRunner:
    """Run fixed, trusted steps; plans cannot alter failure policy."""

    def __init__(
        self,
        steps: list[InstallStep],
        progress: Callable[[str, int, int], None] | None = None,
        status: Callable[[str, StepStatus, str], None] | None = None,
    ):
        self.steps = tuple(steps)
        self.progress = progress or (lambda _step, _done, _total: None)
        self.status = status or (
            lambda _step, _status, _message: None
        )

    def run(self, context: InstallContext) -> InstallResult:
        total = sum(max(1, step.progress_weight) for step in self.steps)
        completed = 0
        results: list[StepResult] = []
        executed: list[InstallStep] = []

        # All preflight checks run before the first destructive operation.
        for step in self.steps:
            context.log(f"[preflight:{step.id}] {step.title}")
            try:
                step.preflight(context)
            except Exception as error:
                message = f"Preflight failed for {step.id}: {error}"
                context.log(message)
                self.status(step.id, StepStatus.FAILED, message)
                results.append(
                    StepResult(step.id, StepStatus.FAILED, message)
                )
                return InstallResult(False, tuple(results), False)

        for step in self.steps:
            weight = max(1, step.progress_weight)
            self.progress(step.id, completed, total)
            self.status(step.id, StepStatus.RUNNING, "")
            context.log(f"[{step.id}] {step.title}")
            if step.destructive:
                context.destructive_started = True
            try:
                step.execute(context)
                step.verify(context)
                executed.append(step)
                self.status(step.id, StepStatus.SUCCEEDED, "")
                results.append(StepResult(step.id, StepStatus.SUCCEEDED))
            except StepWarning as error:
                message = str(error)
                context.log(f"[{step.id}] warning: {message}")
                self._cleanup(context, [step])
                self.status(step.id, StepStatus.WARNING, message)
                results.append(
                    StepResult(step.id, StepStatus.WARNING, message)
                )
            except Exception as error:
                message = str(error)
                context.log(f"[{step.id}] failed: {message}")
                self._cleanup(context, [step])
                if step.failure_policy is FailurePolicy.FATAL:
                    self.status(step.id, StepStatus.FAILED, message)
                    results.append(
                        StepResult(step.id, StepStatus.FAILED, message)
                    )
                    self._cleanup(context, executed)
                    return InstallResult(
                        False, tuple(results), context.destructive_started
                    )
                if step.failure_policy is FailurePolicy.WARNING:
                    self.status(step.id, StepStatus.WARNING, message)
                    results.append(
                        StepResult(step.id, StepStatus.WARNING, message)
                    )
                else:
                    self.status(step.id, StepStatus.SKIPPED, message)
                    results.append(
                        StepResult(step.id, StepStatus.SKIPPED, message)
                    )
            completed += weight

        self.progress("complete", total, total)
        return InstallResult(True, tuple(results), context.destructive_started)

    @staticmethod
    def _cleanup(
        context: InstallContext, executed: list[InstallStep]
    ) -> None:
        for step in reversed(executed):
            try:
                step.cleanup(context)
            except Exception as error:
                context.log(f"[{step.id}] cleanup failed: {error}")
