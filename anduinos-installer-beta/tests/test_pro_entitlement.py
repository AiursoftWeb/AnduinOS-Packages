"""Installation-session entitlement affects the real storage plan."""
import unittest
import io
from dataclasses import replace
from unittest.mock import patch
from helpers import valid_plan
from installer_core.destructive_test import execution_policy
from installer_core.layout import build_erase_disk_layout
from installer_core.model import Architecture, EncryptionMode, Filesystem
from installer_core.validation import ExecutionPolicy, PlanValidationError, validate_plan_for_execution

class ProEntitlementTests(unittest.TestCase):
    def test_session_capability_reaches_the_real_executor_command(self):
        from frontend import ExecutorClient
        class Input(io.StringIO):
            def close(self):
                pass
        class Process:
            def __init__(self):
                self.stdin = Input()
                self.stdout = io.StringIO('{"event":"complete","error":""}\n')
                self.stderr = io.StringIO()
            def wait(self):
                return 0
        for pro in (False, True):
            with self.subTest(pro=pro), patch("frontend.subprocess.Popen", return_value=Process()) as spawn:
                plan = valid_plan(include_recovery=pro)
                result, error = ExecutorClient("/test/executor", has_pro_entitlement=pro).run(
                    plan, lambda *_: None, lambda *_: None)
                self.assertTrue(result, error)
                self.assertEqual("--pro-entitlement" in spawn.call_args.args[0], pro)

    def test_command_line_sets_session_state_only_for_explicit_bypass(self):
        import main
        for argv, expected in ((["installer"], False),
                               (["installer", "--bypass-payment"], True)):
            with self.subTest(argv=argv), patch("sys.argv", argv), patch.object(main, "InstallerApplication") as app:
                main.main()
                app.assert_called_once_with(False, expected)
                app.return_value.run.assert_called_once_with(["installer"])

    def test_erase_layout_and_executor_agree_on_recovery(self):
        for arch in Architecture:
            for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
                for pro in (False, True):
                    with self.subTest(arch=arch, filesystem=filesystem, pro=pro):
                        plan = valid_plan(architecture=arch, filesystem=filesystem, include_recovery=pro)
                        names = [x.name for x in build_erase_disk_layout(plan).partitions]
                        self.assertEqual('recovery' in names, pro)
                        validate_plan_for_execution(plan, ExecutionPolicy.PRO if pro else ExecutionPolicy.RELEASE)
                        if pro:
                            with self.assertRaises(PlanValidationError):
                                validate_plan_for_execution(plan, ExecutionPolicy.RELEASE)

    def test_encryption_requires_pro_executor_capability(self):
        plan = valid_plan(encryption=EncryptionMode.LUKS2)
        with self.assertRaises(PlanValidationError):
            validate_plan_for_execution(plan, ExecutionPolicy.RELEASE)
        validate_plan_for_execution(plan, ExecutionPolicy.PRO)

    def test_pro_does_not_disable_account_password_requirement(self):
        from installer_core.model import AuthenticationMode
        base = valid_plan(authentication=AuthenticationMode.PASSWORDLESS_SHARED)
        with self.assertRaises(PlanValidationError):
            validate_plan_for_execution(base, ExecutionPolicy.PRO)
        self.assertEqual(execution_policy(['--pro-entitlement'], {}), ExecutionPolicy.PRO)
