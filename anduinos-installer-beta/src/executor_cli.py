"""Root-only JSON-lines entry point for the installer executor."""

from __future__ import annotations

import json
import os
import sys

from installer_core.executor import InstallerExecutor
from installer_core.model import InstallPlan
from installer_core.validation import validate_plan


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    if os.geteuid() != 0:
        emit({"event": "complete", "error": "Executor must run as root"})
        return 1
    try:
        line = sys.stdin.readline()
        if not line:
            raise ValueError("No installation plan was provided")
        plan = InstallPlan.from_dict(json.loads(line))
        validate_plan(plan)

        executor = InstallerExecutor(
            lambda message: emit({"event": "log", "message": message}),
            lambda step, done, total: emit(
                {
                    "event": "progress",
                    "step": step,
                    "done": done,
                    "total": total,
                }
            ),
        )
        result = executor.run(plan)
        if not result.succeeded:
            error = next(
                (
                    item.message
                    for item in reversed(result.results)
                    if item.message
                ),
                "Installation failed",
            )
            emit({"event": "complete", "error": error})
            return 1
        emit({"event": "complete", "error": ""})
        return 0
    except Exception as error:
        emit({"event": "complete", "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

