import unittest

from helpers import valid_plan
from installer_core.executor import InstallerExecutor
from installer_core.model import Architecture, Filesystem
from test_guided_storage_graph import guided_plan
from test_manual_storage_graph import manual_plan
from pages import ordered_progress_steps, incomplete_feature_steps


class ProgressPipelineTests(unittest.TestCase):
    def test_completion_lists_missing_features_not_recovered_network(self):
        self.assertEqual(incomplete_feature_steps({
            "detect-network-connectivity": "warning",
            "recheck-network-connectivity": "succeeded",
            "install-input-method": "succeeded",
            "install-multimedia-codecs": "warning",
            "upgrade-system": "warning",
            "install-third-party-drivers": "skipped",
        }), ("install-multimedia-codecs", "upgrade-system"))
        self.assertEqual(incomplete_feature_steps({
            "detect-network-connectivity": "warning",
            "recheck-network-connectivity": "succeeded",
            "install-input-method": "succeeded",
            "install-multimedia-codecs": "succeeded",
        }), ())

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

    def test_fallback_step_is_between_bootloader_and_enrollment_only_for_new_esp(self):
        for arch in Architecture:
            plans = (valid_plan(architecture=arch),
                     guided_plan(reuse_esp=False, architecture=arch)[0],
                     guided_plan(architecture=arch)[0], manual_plan(architecture=arch)[0])
            for plan in plans:
                with self.subTest(architecture=arch, mode=plan.storage.mode,
                                  new_esp=plan.storage.graph.creates_esp):
                    ids = [step.id for step in InstallerExecutor(lambda _: None).build_steps(plan)]
                    self.assertEqual("create-fallback-boot-chain" in ids, plan.storage.graph.creates_esp)
                    if plan.storage.graph.creates_esp:
                        self.assertLess(ids.index("install-bootloader"), ids.index("create-fallback-boot-chain"))
                        self.assertLess(ids.index("create-fallback-boot-chain"), ids.index("enroll-secure-boot"))

    def test_every_canonical_step_requires_a_title(self):
        plan = valid_plan()

        with self.assertRaisesRegex(
            RuntimeError,
            "Missing progress titles for canonical pipeline steps",
        ):
            ordered_progress_steps(plan, {})


if __name__ == "__main__":
    unittest.main()
