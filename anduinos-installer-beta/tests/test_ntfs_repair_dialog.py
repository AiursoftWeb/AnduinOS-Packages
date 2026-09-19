"""The disk-check warning offers instructions, never a destructive override."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from i18n import _
from installer_core.ntfs_resize import NtfsResizeBlockReason
from languages import DEFAULT_LANGUAGE, LANGUAGES


PACKAGE = Path(__file__).resolve().parents[1]
SOURCES = (
    "View Repair Steps", "Repair Steps",
    "This NTFS volume requires a disk check in Windows before it can be resized safely.",
)


class NtfsRepairDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((PACKAGE / "src/pages.py").read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name in {
                         "_show_resize_blocked", "_show_resize_inspection_blocked",
                         "_resize_block_message",
                     }]
        cls.code = compile(ast.Module(body=functions, type_ignores=[]), "pages.py", "exec")

    def setUp(self):
        self.dialogs = []

        def dialog(**kwargs):
            widget = Mock()
            widget.properties = kwargs
            self.dialogs.append(widget)
            return widget

        self.namespace = {
            "Gtk": SimpleNamespace(Label=Mock(side_effect=lambda **kw: Mock(properties=kw))),
            "Adw": SimpleNamespace(MessageDialog=dialog),
            "nav_view": Mock(), "lang": "zh_CN", "_": _,
            "NtfsResizeBlockReason": NtfsResizeBlockReason,
        }
        exec(self.code, self.namespace)

    def show_required_check(self):
        self.namespace["_show_resize_inspection_blocked"](SimpleNamespace(
            block_reason=NtfsResizeBlockReason.CHECK_REQUIRED,
            message="Run chkdsk /f and please try again, or see option -f.",
        ))
        return self.dialogs[0]

    def test_warning_has_only_close_and_instructions_and_defaults_to_close(self):
        dialog = self.show_required_check()
        self.assertEqual([("close", "关闭"), ("repair-steps", "查看修复步骤")],
                         [call.args for call in dialog.add_response.call_args_list])
        dialog.set_default_response.assert_called_once_with("close")
        dialog.set_close_response.assert_called_once_with("close")
        body = dialog.properties["extra_child"].properties["label"]
        self.assertEqual(body, _(SOURCES[2], "zh_CN"))
        self.assertNotIn("chkdsk", body)
        callback = dialog.connect.call_args.args[1]
        callback(dialog, "close")
        self.assertEqual(len(self.dialogs), 1)

    def test_view_steps_opens_only_selectable_instructions(self):
        dialog = self.show_required_check()
        dialog.connect.call_args.args[1](dialog, "repair-steps")
        self.assertEqual(len(self.dialogs), 2)
        steps = self.dialogs[1]
        self.assertEqual(steps.properties["heading"], "修复步骤")
        text = steps.properties["extra_child"].properties
        self.assertTrue(text["selectable"])
        self.assertIn("chkdsk X: /f", text["label"])
        self.assertNotIn("chkdsk C:", text["label"])
        steps.add_response.assert_called_once_with("close", "关闭")
        steps.present.assert_called_once()

    def test_unrelated_errors_keep_their_original_message_and_close_button(self):
        self.namespace["_show_resize_inspection_blocked"](SimpleNamespace(
            block_reason=NtfsResizeBlockReason.RANGE_UNAVAILABLE,
            message="Device read failed",
        ))
        dialog = self.dialogs[0]
        self.assertEqual(dialog.properties["extra_child"].properties["label"], "Device read failed")
        dialog.add_response.assert_called_once_with("close", "关闭")
        dialog.connect.assert_not_called()

    def test_new_labels_are_translated_in_all_28_languages(self):
        self.assertEqual(len(LANGUAGES), 28)
        for language in LANGUAGES:
            for source in SOURCES:
                with self.subTest(language=language.code, source=source):
                    translated = _(source, language.code)
                    self.assertTrue(translated)
                    if language.code not in {DEFAULT_LANGUAGE, "en_GB"}:
                        self.assertNotEqual(translated, source)
