import hashlib
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).parents[1]
ICONS = ROOT / "assets" / "icons"


class InstallerVisualAssetTests(unittest.TestCase):
    def test_account_flows_through_secure_default_advanced_options(self):
        pages = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        main = (ROOT / "src/main.py").read_text(encoding="utf-8")
        user_page = pages.split("def build_user_page", 1)[1].split(
            "def _labeled", 1
        )[0]
        advanced_page = pages.split(
            "def build_advanced_options_page", 1
        )[1].split("def build_timezone_page", 1)[0]
        self.assertIn("build_advanced_options_page", user_page)
        self.assertIn('_("A password is required.", lang)', user_page)
        self.assertNotIn("passwordless_shared", user_page)
        for key in (
            "sudo_without_password",
            "automatic_login",
            "ssh_password_login",
        ):
            self.assertIn(f'"{key}": False', main)
            self.assertIn(f'"{key}"', advanced_page)
        self.assertEqual(advanced_page.count("_choice_row("), 4)
        self.assertIn("Adw.PreferencesGroup(", advanced_page)
        self.assertIn("Adw.SwitchRow(", advanced_page)
        self.assertIn('row.connect("notify::active"', advanced_page)
        self.assertNotIn("Gtk.CheckButton(", advanced_page)
        self.assertIn("icon_picture(icon_name, 40)", advanced_page)
        for icon_name in ("sudo-no-pass", "login-directly", "allow-ssh"):
            self.assertIn(f'"{icon_name}"', advanced_page)
        self.assertIn("_update_security_warning()", advanced_page)
        self.assertIn('warning.add_css_class("caption")', advanced_page)
        self.assertNotIn("(unsafe)", advanced_page)
        self.assertNotIn(
            "These options reduce the security of the installed system",
            advanced_page,
        )
        self.assertNotIn("enabled (unsafe)", pages)

    def test_snapshots_manager_retains_the_anduinos_owned_artwork(self):
        digest = hashlib.sha256((ICONS / "disk-snapshots-manager.svg").read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "f6d678d9551cbeb64c4fcad189d1b34aaaad59465588eee7b504cd0c798729a3",
        )

    def test_secure_boot_page_uses_the_oobe_circuit_board_artwork(self):
        digest = hashlib.sha256(
            (ICONS / "secure-boot.svg").read_bytes()
        ).hexdigest()
        self.assertEqual(
            digest,
            "7ade745cee9037160c5dcc5bb06837a15ad93681a74729283707a7d69b348778",
        )
        pages = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        secure_boot_page = pages.split("def build_secure_boot_page", 1)[1]
        secure_boot_page = secure_boot_page.split("def build_network_page", 1)[0]
        self.assertIn('            "secure-boot",', secure_boot_page)
        self.assertNotIn('"security-high-symbolic"', secure_boot_page)

    def test_progress_ui_exposes_regional_steps_and_warnings(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        self.assertIn('"configure-keyboard-layout":', source)
        self.assertIn('"install-language-packs":', source)
        self.assertIn(
            '"Ensure required language packs are installed", lang', source
        )
        self.assertIn('"install-input-method":', source)
        self.assertIn('_("Install input method", lang)', source)
        self.assertIn('", ".join(', source)
        self.assertIn('if status == "warning":', source)

    def test_optional_downloads_share_visible_offline_state(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        self.assertIn("def _offline_callout", source)
        self.assertIn("Gtk.CheckButton(", source)
        self.assertIn("choice.set_sensitive(online)", source)
        self.assertIn('shared["input_methods"] = selected', source)
        self.assertIn("updates.set_sensitive(online)", source)
        self.assertIn("drivers.set_sensitive(online)", source)
        self.assertIn("selected_methods", source)
        self.assertIn("method.language_name", source)

    def test_keyboard_tester_uses_raw_keycodes_and_private_xkb_state(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        self.assertIn("XkbKeyboardPreview", source)
        self.assertIn("Gtk.EventControllerKey()", source)
        self.assertIn(
            "set_propagation_phase(Gtk.PropagationPhase.CAPTURE)", source
        )
        self.assertIn("preview.press(keycode)", source)
        self.assertIn("preview.release(keycode)", source)
        self.assertIn("keyboard_layouts()", source)
        self.assertIn("layout_dropdown.set_enable_search(True)", source)
        self.assertIn("variant_dropdown.set_enable_search(True)", source)
        self.assertIn('shared["keyboard_variant"] = variant_id', source)
        self.assertNotIn("gsettings set", source)
        self.assertNotIn("setxkbmap", source)

    def test_every_wizard_illustration_is_a_parseable_local_svg(self):
        expected = {
            "welcome.svg",
            "language.svg",
            "network.svg",
            "keyboard.svg",
            "updates.svg",
            "disk.svg",
            "disk-snapshots-manager.svg",
            "coexistence.svg",
            "secure-boot.svg",
            "account.svg",
            "timezone.svg",
            "review.svg",
            "advanced.svg",
            "allow-ssh.svg",
            "login-directly.svg",
            "sudo-no-pass.svg",
            "btrfs.svg",
            "ext4.svg",
            "flashing-disk.svg",
            "how-should-use.svg",
            "one-single-disk.svg",
            "select-installation-disk.svg",
        }
        self.assertEqual(
            {path.name for path in ICONS.glob("*.svg")},
            expected,
        )
        for name in sorted(expected):
            with self.subTest(name=name):
                root = ET.parse(ICONS / name).getroot()
                self.assertTrue(root.tag.endswith("svg"))

    def test_stylesheet_defines_the_shared_visual_contract(self):
        style = (ROOT / "assets" / "style.css").read_text()
        for selector in (
            ".installer-hero",
            ".installer-card",
            ".disk-card-list",
            ".partition-chip",
            ".strategy-card",
            ".wizard-navigation",
            ".wizard-dot-active",
            ".installer-progress",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, style)

    def test_list_selection_and_welcome_layout_have_one_visual_boundary(self):
        style = (ROOT / "assets" / "style.css").read_text()
        pages = (ROOT / "src/pages.py").read_text()
        welcome = pages.split("def build_welcome_page", 1)[1].split(
            "def build_low_battery_page", 1
        )[0]

        self.assertIn(".installer-list-view row:selected {", style)
        self.assertIn("background-color: transparent;", style)
        self.assertIn(
            '.add_css_class("installer-list-view")',
            welcome,
        )
        self.assertIn("layout = Gtk.Box(", welcome)
        self.assertNotIn("Gtk.Paned(", welcome)

    def test_scrollbars_use_one_non_overlay_layout_policy(self):
        pages = (ROOT / "src/pages.py").read_text()

        self.assertEqual(pages.count("Gtk.ScrolledWindow("), 1)
        self.assertIn(
            'properties.setdefault("vscrollbar_policy", '
            "Gtk.PolicyType.AUTOMATIC)",
            pages,
        )
        self.assertIn("scroll.set_overlay_scrolling(False)", pages)
        for name in (
            "disk_scroll",
            "controls_scroll",
            "account_scroll",
            "options_scroll",
            "tz_scroll",
            "summary_scroll",
        ):
            with self.subTest(name=name):
                construction = pages.split(f"{name} = ", 1)[1].split(
                    ")", 1
                )[0]
                self.assertIn("inset=True", construction)

    def test_copied_illustrations_have_package_local_provenance(self):
        provenance = (ICONS / "README.md").read_text()
        for name in sorted(path.name for path in ICONS.glob("*.svg")):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", provenance)
        self.assertIn("GPL-3.0", provenance)

    def test_storage_cards_expose_layout_without_redundant_disk_copy(self):
        pages = (ROOT / "src/pages.py").read_text()
        for fragment in (
            "disk.partitions",
            "disk.free_extents",
            "extent.size_bytes >= _LAYOUT_FREE_SPACE_MINIMUM_BYTES",
            "partition.filesystem_type",
            "row.append(icon_picture(icon, 56))",
            '        "btrfs",',
            '        "ext4",',
            '        "advanced",',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, pages)
        self.assertNotIn(
            "Only this disk can be partitioned or formatted",
            pages,
        )

    def test_storage_method_header_carries_disk_info_without_target_badge(self):
        pages = (ROOT / "src/pages.py").read_text()
        method_page = pages.split("def build_storage_strategy_page", 1)[1]
        method_page = method_page.split("def ", 1)[0]
        self.assertIn('shared.get("disk_model", "?")', method_page)
        self.assertIn('shared.get("disk_size", "?")', method_page)
        self.assertIn('shared.get("disk", "?")', method_page)
        self.assertNotIn("target_box", method_page)
        self.assertNotIn("Target:", method_page)

    def test_review_exposes_partition_plan_and_expandable_swap_formula(self):
        pages = (ROOT / "src/pages.py").read_text()
        summary = pages.split("def build_summary_page", 1)[1]
        for fragment in (
            "build_erase_disk_layout_spec(",
            'f"#{item.number}"',
            "Gtk.Expander(",
            "⚙ AUTO ⓘ",
            '"swap ≥ 2 GiB"',
            '"/ ≥ 20 GiB"',
            'f"⇒ swap = {swap_gib} GiB"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, summary)

    def test_hostname_uses_shared_normalization_without_a_page_local_regex(self):
        pages = (ROOT / "src/pages.py").read_text()
        account = pages.split("def build_user_page", 1)[1]
        account = account.split("def _labeled", 1)[0]
        self.assertIn("normalize_hostname(host)", account)
        self.assertIn("normalize_hostname(host_entry.get_text())", account)
        self.assertNotIn("HOST_RE", account)

    def test_final_plan_is_built_before_destructive_confirmation(self):
        pages = (ROOT / "src/pages.py").read_text()
        summary = pages.split("def build_summary_page", 1)[1]
        summary = summary.split("def ordered_progress_steps", 1)[0]
        apply_recheck = summary.split("def _apply_recheck", 1)[1]
        apply_recheck = apply_recheck.split("def _start_recheck", 1)[0]
        self.assertIn("create_install_plan(", apply_recheck)
        self.assertIn("clear_passwords=False", apply_recheck)
        self.assertIn("_show_install_confirmation(plan)", apply_recheck)

        confirmation = summary.split(
            "def _show_install_confirmation", 1
        )[1].split("def on_install", 1)[0]
        self.assertIn("plan.identity.hostname", confirmation)
        self.assertIn("clear_plaintext_passwords(shared)", confirmation)
        self.assertIn("build_progress_page(plan, shared, nav_view)", confirmation)
        self.assertNotIn("_start_recheck()", confirmation)

        on_install = summary.split("def on_install", 1)[1]
        self.assertIn("_start_recheck()", on_install)


if __name__ == "__main__":
    unittest.main()
