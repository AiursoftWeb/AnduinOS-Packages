from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent


class PackageTests(unittest.TestCase):
    def test_python_sources_compile_without_cache_files(self):
        for source in [
            ROOT / "src/anduinos-control-panel",
            *sorted((ROOT / "src/anduinos_control_panel").glob("*.py")),
        ]:
            compile(source.read_text(), str(source), "exec")

    def test_desktop_entry_uses_the_stable_application_id(self):
        desktop = (ROOT / "data/com.anduinos.ControlPanel.desktop").read_text()
        self.assertIn("Exec=anduinos-control-panel", desktop)
        self.assertIn("Icon=com.anduinos.ControlPanel", desktop)
        self.assertIn("Categories=Settings;System;", desktop)

    def test_appstream_includes_both_provided_screenshots(self):
        project = (ROOT / "anduinos-control-panel.aosproj").read_text()
        self.assertIn(
            '<AppStreamScreenshot Include="screenshots/control-panel.png" '
            'Default="true"',
            project,
        )
        self.assertIn(
            '<AppStreamScreenshot Include="screenshots/about.png"',
            project,
        )
        for name in ("control-panel.png", "about.png"):
            screenshot = ROOT / "screenshots" / name
            self.assertTrue(screenshot.is_file())
            self.assertEqual(screenshot.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_window_defaults_to_a_balanced_two_column_size(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn("self.set_default_size(1166, 762)", application)
        self.assertIn("column_homogeneous=True", application)
        self.assertIn("self.grid.attach(child, index % 2, index // 2, 1, 1)", application)
        self.assertIn("maximum_size=800", application)

    def test_header_does_not_show_a_manual_refresh_button(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertNotIn('icon_name="view-refresh-symbolic"', application)
        self.assertNotIn('_("Refresh availability")', application)

    def test_category_icons_share_one_rendered_boundary(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        project = (ROOT / "anduinos-control-panel.aosproj").read_text()
        self.assertIn("GdkPixbuf.Pixbuf.new_from_file_at_scale(", application)
        self.assertIn("str(_icon_path(name)), 56, 56, True", application)
        self.assertIn("icon_frame.set_size_request(60, 60)", application)
        self.assertIn("root = Gtk.Grid(column_spacing=14)", application)
        self.assertIn("root.attach(body, 1, 0, 1, 1)", application)
        self.assertIn('<Dependency Include="gir1.2-gdkpixbuf-2.0" />', project)

    def test_category_layout_uses_compact_windows_control_panel_hierarchy(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn("color: @success_color", application)
        self.assertIn("color: @accent_color", application)
        self.assertIn("row_spacing=18", application)
        self.assertNotIn('Gtk.Image.new_from_icon_name("go-next-symbolic")', application)

    def test_action_links_follow_windows_7_hover_behavior(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn('button.set_cursor_from_name("pointer")', application)
        self.assertIn("Pango.attr_underline_new(Pango.Underline.SINGLE)", application)
        self.assertIn("motion = Gtk.EventControllerMotion()", application)
        self.assertIn('motion.connect("enter", pointer_entered)', application)
        self.assertIn('motion.connect("leave", pointer_left)', application)
        self.assertIn('button.connect("notify::has-focus"', application)
        self.assertNotIn("outline: none", application)

    def test_launcher_reports_asynchronous_process_failures(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn("Gio.SubprocessFlags.STDOUT_PIPE", application)
        self.assertIn("Gio.SubprocessFlags.STDERR_PIPE", application)
        self.assertIn("process.communicate_utf8_async(", application)
        self.assertIn("launched_process.communicate_utf8_finish(", application)
        self.assertIn("if launched_process.get_successful():", application)
        self.assertIn("details = (stderr or stdout or \"\").strip()", application)

    def test_all_requested_categories_and_launchers_are_present(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        for title in (
            "System",
            "Security",
            "Network and Internet",
            "AI Stack",
            "Windows Compatibility",
            "Hardware and Drivers",
            "Programs",
            "User Accounts",
            "Appearance",
            "Backup and Recovery",
            "Accessibility",
        ):
            self.assertIn(f'_("{title}")', application)

        for command in (
            '["gnome-control-center"]',
            '["swapcontrol-gtk"]',
            '["anduinos-driver-center", "--page", "secure-boot"]',
            '["seahorse"]',
            '["ufwall-gtk"]',
            '["nm-connection-editor"]',
            '["flatpak", "run", BOTTLES_APP_ID]',
            '["anduinos-driver-center"]',
            '["gnome-control-center", "printers"]',
            '["gnome-software", "--mode=installed"]',
            '["com.github.tchx84.Flatseal"]',
            '["gnome-control-center", "system", "users"]',
            '["anduinos-yubikey-manager"]',
            '["anduinos-appearance"]',
            '["gnome-control-center", "background"]',
            '["anduinos-btrfs-snapshots-manager"]',
            '["flatpak", "run", DEJA_DUP_APP_ID]',
        ):
            self.assertIn(command, application)

        self.assertIn('_("System Snapshots")', application)
        self.assertNotIn('_("Btrfs Snapshots")', application)
        self.assertIn('_("Wallpaper and Accent Color")', application)

    def test_optional_entries_are_gated_by_runtime_state(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertNotIn("if secure_boot_enabled():", application)
        self.assertNotIn("secure_boot_enabled,", application)
        self.assertIn('command_available("seahorse")', application)
        self.assertIn('command_available("nm-connection-editor")', application)
        self.assertIn("if package_installed(SNAPSHOT_PACKAGE):", application)
        self.assertIn("if flatpak_installed(BOTTLES_APP_ID):", application)
        self.assertIn("if flatpak_installed(DEJA_DUP_APP_ID):", application)
        self.assertIn('if package_installed("flatseal"):', application)

    def test_voice_typing_is_discoverable_but_installed_only_on_request(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        project = (ROOT / "anduinos-control-panel.aosproj").read_text()
        self.assertIn("VOICE_TYPING_PACKAGE", application)
        self.assertIn('self._launch(["anduinos-whisper-gtk"])', application)
        self.assertIn('title=_("Install Voice Typing")', application)
        self.assertIn('_("About 140 MB to download")', application)
        self.assertIn('_("Speech is recognized locally with whisper.cpp")', application)
        self.assertIn(
            '["gnome-extensions", "enable", "--quiet", extension_uuid]',
            application,
        )
        self.assertNotIn("org.gnome.Shell.Extensions.ReloadExtension", application)
        self.assertIn("Sign out and back in once, then press", application)
        self.assertIn("Super + H to start Voice Typing", application)
        self.assertNotIn('_("✓ Voice Typing is ready.")', application)
        self.assertIn('Gio.Settings.new("org.gnome.shell")', application)
        self.assertNotIn('<Dependency Include="anduinos-whisper', project)

    def test_ai_and_flatseal_changes_use_fixed_apt_arguments(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn('WHY_AI_PACKAGE if enabled else WHY_PLACEHOLDER_PACKAGE', application)
        self.assertIn(
            'self._run_streaming_package_change(\n            "flatseal"', application
        )
        self.assertIn('"/usr/bin/pkexec"', application)
        self.assertIn('"/usr/bin/apt-get"', application)
        self.assertIn('"install",', application)
        self.assertIn('"--yes",', application)
        self.assertIn("stdout=subprocess.PIPE", application)
        self.assertIn("stderr=subprocess.STDOUT", application)
        self.assertIn("def _run_streaming_commands(", application)
        self.assertNotIn("shell=True", application)
        self.assertNotIn("bash -c", application)

    def test_ai_apply_button_starts_disabled_and_tracks_changes(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn("apply.set_sensitive(False)", application)
        self.assertIn("row.get_active() != installed", application)
        self.assertIn('apply.add_css_class("suggested-action")', application)

    def test_slow_ai_install_shows_live_advanced_output(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn("default_width=560", application)
        self.assertIn("default_height=360", application)
        self.assertIn('Gtk.Expander(label=_("Advanced Output"))', application)
        self.assertIn('expander.connect("notify::expanded", advanced_output_toggled)', application)
        self.assertIn("680 if row.get_expanded() else 560", application)
        self.assertIn("560 if row.get_expanded() else 360", application)
        self.assertIn("output = Gtk.TextView(", application)
        self.assertIn("monospace=True", application)
        self.assertIn("progress.pulse()", application)
        self.assertIn("This may take about 10 minutes.", application)
        self.assertIn("process = subprocess.Popen(", application)
        self.assertIn("stdout=subprocess.PIPE", application)
        self.assertIn("stderr=subprocess.STDOUT", application)
        self.assertIn('for line in iter(process.stdout.readline, ""):', application)
        self.assertIn(
            "self._append_package_output, buffer, output, line", application
        )
        self.assertIn("window.set_deletable(False)", application)

    def test_flatseal_install_has_intro_progress_and_advanced_output(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn('title=_("Permission Settings")', application)
        self.assertIn('title=_("Flatseal")', application)
        self.assertIn('start = Gtk.Button(label=_("Start"))', application)
        self.assertIn('start.set_label(_("Installing…"))', application)
        self.assertIn('start.set_label(_("Open Flatseal"))', application)
        self.assertIn('start.set_label(_("Retry"))', application)
        self.assertGreaterEqual(
            application.count('Gtk.Expander(label=_("Advanced Output"))'), 2
        )
        self.assertIn("self._install_flatseal(", application)
        self.assertIn("buffer.set_text(\n            _(", application)
        self.assertIn('"--yes",\n            package,', application)
        self.assertIn("window.set_deletable(False)", application)

    def test_bottles_install_has_intro_progress_and_advanced_output(self):
        application = (ROOT / "src/anduinos_control_panel/app.py").read_text()
        self.assertIn('title=_("Windows Compatibility")', application)
        self.assertIn('title=_("Bottles")', application)
        self.assertIn('title=_("Install Bottles")', application)
        self.assertIn("self._install_bottles(", application)
        self.assertIn('start.set_label(_("Open Bottles"))', application)
        self.assertGreaterEqual(
            application.count('Gtk.Expander(label=_("Advanced Output"))'), 3
        )
        self.assertIn('"remote-add",', application)
        self.assertIn('"--if-not-exists",', application)
        self.assertIn('"install",\n                "--system",', application)
        self.assertIn('"--assumeyes",', application)
        self.assertNotIn('"--noninteractive",', application)
        self.assertIn("FLATHUB_REPOSITORY", application)
        self.assertNotIn(
            'self._show_store_prompt(_("Bottles")', application
        )

    def test_project_reuses_parseable_repository_svg_assets(self):
        project = (ROOT / "anduinos-control-panel.aosproj").read_text()
        self.assertIn(
            '<IncludeFolder Include="resources/icons" '
            'Target="/usr/share/anduinos-control-panel/icons" />',
            project,
        )
        self.assertIn(
            '<IncludeFile Include="resources/icons/com.anduinos.ControlPanel.svg" '
            'Target="/usr/share/icons/hicolor/scalable/apps/com.anduinos.ControlPanel.svg" />',
            project,
        )
        icons = sorted((ROOT / "resources/icons").glob("*.svg"))
        self.assertEqual(len(icons), 13)
        self.assertIn("com.anduinos.ControlPanel.svg", {icon.name for icon in icons})
        self.assertIn(
            "com.anduinos.ControlPanel-symbolic.svg", {icon.name for icon in icons}
        )
        self.assertIn("com.anduinos.DriverCenter.svg", {icon.name for icon in icons})
        self.assertIn("anduinos-appearance.svg", {icon.name for icon in icons})
        self.assertIn("anduinos-exe-runner.svg", {icon.name for icon in icons})
        self.assertIn("com.anduinos.yubikeymanager.svg", {icon.name for icon in icons})
        self.assertIn("audio-input-microphone.svg", {icon.name for icon in icons})
        for svg in icons:
            self.assertTrue(ET.parse(svg).getroot().tag.endswith("svg"), svg.name)

        app_icon = (ROOT / "resources/icons/com.anduinos.ControlPanel.svg").read_text()
        self.assertNotIn("<image", app_icon)

        appearance_icon = (
            REPOSITORY / "anduinos-appearance/data/anduinos-appearance.svg"
        ).read_text()
        vendored_appearance_icon = (
            ROOT / "resources/icons/anduinos-appearance.svg"
        ).read_text()
        self.assertIn('fill="#38a0d4"', app_icon)
        self.assertEqual(app_icon.count("<circle"), 3)
        self.assertIn('fill="#2268ab"', appearance_icon)
        self.assertEqual(appearance_icon.count("<path"), 9)
        self.assertEqual(vendored_appearance_icon, appearance_icon)

        symbolic_icon = (
            ROOT / "resources/icons/com.anduinos.ControlPanel-symbolic.svg"
        ).read_text()
        symbolic_root = ET.fromstring(symbolic_icon)
        self.assertEqual(symbolic_root.attrib.get("width"), "16")
        self.assertEqual(symbolic_root.attrib.get("height"), "16")
        self.assertNotIn("<image", symbolic_icon)
        self.assertNotIn("#2268ab", symbolic_icon)
        self.assertIn(
            'Target="/usr/share/icons/hicolor/symbolic/apps/'
            'com.anduinos.ControlPanel-symbolic.svg"',
            project,
        )

    def test_fixed_anduinos_launchers_are_hard_dependencies(self):
        project = (ROOT / "anduinos-control-panel.aosproj").read_text()
        for package in (
            "anduinos-driver-center",
            "anduinos-appearance",
            "anduinos-ufwall-gtk",
            "anduinos-swapcontrol-gtk",
            "anduinos-yubikey-manager",
            "anduinos-btrfs-snapshots-manager",
        ):
            self.assertIn(f'<Dependency Include="{package}" />', project)
            self.assertNotIn(f'<Suggest Include="{package}"', project)
        self.assertNotIn('<Suggest Include="anduinos-btrfs-snapshots-manager', project)

    def test_control_panel_is_published_for_resolute_only(self):
        project = ET.parse(ROOT / "anduinos-control-panel.aosproj").getroot()
        target_suites = project.findtext(".//TargetSuites")
        self.assertEqual(target_suites, "resolute-addon")

    def test_default_desktop_selection_recommends_the_control_panel(self):
        desktop_apps = (
            REPOSITORY / "anduinos-desktop-apps/anduinos-desktop-apps.aosproj"
        ).read_text()
        self.assertIn('<Recommend Include="anduinos-control-panel" />', desktop_apps)


if __name__ == "__main__":
    unittest.main()
