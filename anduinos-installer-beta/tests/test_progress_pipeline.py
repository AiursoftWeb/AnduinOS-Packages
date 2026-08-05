import unittest

from helpers import valid_plan
from installer_core.executor import InstallerExecutor
from installer_core.model import Filesystem
from pages import ordered_progress_steps


class ProgressPipelineTests(unittest.TestCase):
    def test_progress_rows_follow_canonical_executor_order(self):
        plans = (
            valid_plan(
                install_updates=True,
                install_third_party_drivers=True,
                install_multimedia_codecs=True,
            ),
            valid_plan(
                filesystem=Filesystem.EXT4,
                install_updates=False,
                install_third_party_drivers=False,
                install_multimedia_codecs=False,
            ),
        )

        for plan in plans:
            with self.subTest(plan=plan):
                canonical = tuple(
                    step.id
                    for step in InstallerExecutor(
                        lambda _message: None
                    ).build_steps(plan)
                )
                titles = {
                    step_id: f"Title for {step_id}"
                    for step_id in canonical
                }
                displayed = tuple(
                    step_id
                    for step_id, _title in ordered_progress_steps(
                        plan, titles
                    )
                )
                self.assertEqual(displayed, canonical)

    def test_every_canonical_step_requires_a_title(self):
        plan = valid_plan()

        with self.assertRaisesRegex(
            RuntimeError,
            "Missing progress titles for canonical pipeline steps",
        ):
            ordered_progress_steps(plan, {})


if __name__ == "__main__":
    unittest.main()
