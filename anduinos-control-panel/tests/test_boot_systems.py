"""GRUB discovery and privileged writes, entirely against temporary files."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_boot_helper import boot_settings_helper as helper


MENU = """### BEGIN /etc/grub.d/00_header ###
if [ "${next_entry}" ]; then
  set default="${next_entry}"
else
  set default="0"
fi
function load_video {
  insmod all_video
}
### END /etc/grub.d/00_header ###
### BEGIN /etc/grub.d/10_linux ###
menuentry 'AnduinOS' --class gnu-linux --class os $menuentry_id_option 'gnulinux-simple-abc' {
  linux /boot/vmlinuz root=UUID=abc
}
submenu 'Advanced options for AnduinOS' $menuentry_id_option 'gnulinux-advanced-abc' {
  menuentry 'AnduinOS, old kernel' --id 'gnulinux-old-abc' {
    linux /boot/vmlinuz-old
  }
  menuentry 'AnduinOS (recovery)' --id 'gnulinux-recovery-abc' {
    linux /boot/vmlinuz single
  }
}
### END /etc/grub.d/10_linux ###
### BEGIN /etc/grub.d/30_os-prober ###
menuentry 'Windows Boot Manager (on /dev/nvme0n1p1)' --class windows --class os $menuentry_id_option 'osprober-efi-1234-ABCD' {
  chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
menuentry 'Other Linux' --class os --id=osprober-gnulinux-simple-def {
  linux /boot/vmlinuz
}
### END /etc/grub.d/30_os-prober ###
### BEGIN /etc/grub.d/30_uefi-firmware ###
menuentry 'UEFI Firmware Settings' --id 'uefi-firmware' {
  fwsetup
}
### END /etc/grub.d/30_uefi-firmware ###
### BEGIN /etc/grub.d/40_custom ###
menuentry 'Custom' --id 'osprober-chain-custom' {
  chainloader /custom
}
### END /etc/grub.d/40_custom ###
"""
WINDOWS = 'osprober-efi-1234-ABCD'


INSTALLER_WINDOWS = """### BEGIN /etc/grub.d/42_anduinos_windows ###
menuentry 'Windows Boot Manager' --class windows --class os {
    insmod part_gpt
    insmod fat
    insmod chain
    search --no-floppy --fs-uuid --set=root 04D4-E356
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
### END /etc/grub.d/42_anduinos_windows ###
"""
SNAPSHOT_OVERRIDE = """### BEGIN /etc/grub.d/01_anduinos_btrfs_snapshots_manager_env ###
if [ -s ($btrfs_snapshots_manager_esp)/EFI/anduinos/btrfs-snapshots-manager-grubenv ]; then
    load_env -f ($btrfs_snapshots_manager_esp)/EFI/anduinos/btrfs-snapshots-manager-grubenv
    if [ "${btrfs_snapshots_manager_next_entry}" ]; then
        set default="${btrfs_snapshots_manager_next_entry}"
        set btrfs_snapshots_manager_next_entry=
        save_env -f "($btrfs_snapshots_manager_esp)/EFI/anduinos/btrfs-snapshots-manager-grubenv" btrfs_snapshots_manager_next_entry
        set boot_once=true
    fi
fi
### END /etc/grub.d/01_anduinos_btrfs_snapshots_manager_env ###
"""


class BootSystemTests(unittest.TestCase):
    def test_native_installer_windows_and_snapshot_override(self):
        systems = helper.boot_systems(MENU + SNAPSHOT_OVERRIDE + INSTALLER_WINDOWS)
        self.assertEqual(systems['current'], 'gnulinux-simple-abc')
        self.assertEqual(systems['entries'][-1]['id'], 'Windows Boot Manager')

    def test_native_multiple_windows_and_duplicate_titles(self):
        menu = MENU + INSTALLER_WINDOWS.replace('Windows Boot Manager', 'Windows Boot Manager (disk 1)')
        menu += INSTALLER_WINDOWS.replace('Windows Boot Manager', 'Windows Boot Manager (disk 2)')
        self.assertEqual([entry['id'] for entry in helper.boot_systems(menu)['entries'][-2:]],
                         ['Windows Boot Manager (disk 1)', 'Windows Boot Manager (disk 2)'])
        duplicate = MENU + INSTALLER_WINDOWS + INSTALLER_WINDOWS.replace('42_anduinos_windows', '40_custom')
        self.assertNotIn('Windows Boot Manager', [entry['id'] for entry in helper.boot_systems(duplicate)['entries']])

    def test_only_known_snapshot_one_shot_override_is_ignored(self):
        for override in (
            SNAPSHOT_OVERRIDE.replace('01_anduinos_btrfs_snapshots_manager_env', '40_custom'),
            SNAPSHOT_OVERRIDE.replace('set default="${btrfs_snapshots_manager_next_entry}"', 'set default="2"'),
        ):
            self.assertIsNone(helper.boot_systems(MENU + override)['current'])

    def test_installer_title_is_quoted_and_verified_when_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.cfg'
            menu = Path(directory) / 'grub.cfg'
            original = MENU + SNAPSHOT_OVERRIDE + INSTALLER_WINDOWS
            menu.write_text(original)
            def regenerate():
                menu.write_text(original.replace('default="0"', 'default="Windows Boot Manager"'))
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'GRUB_MENU', menu), patch.object(helper, 'update_grub', side_effect=regenerate):
                helper.set_settings(10, 'native', 'Windows Boot Manager')
                self.assertIn("GRUB_DEFAULT='Windows Boot Manager'\n", config.read_text())
                self.assertEqual(helper.read_boot_systems()['current'], 'Windows Boot Manager')

    def test_filters_advanced_recovery_firmware_and_custom_entries(self):
        systems = helper.boot_systems(MENU)
        self.assertEqual([e['id'] for e in systems['entries']],
                         ['gnulinux-simple-abc', WINDOWS, 'osprober-gnulinux-simple-def'])
        self.assertEqual(systems['current'], 'gnulinux-simple-abc')

    def test_resolves_id_title_and_index_counting_submenus(self):
        for value in (WINDOWS, '2', 'Windows Boot Manager (on /dev/nvme0n1p1)'):
            with self.subTest(value=value):
                self.assertEqual(helper.boot_systems(MENU.replace('default="0"', f'default="{value}"'))['current'], WINDOWS)

    def test_unknown_saved_and_nested_defaults_are_preserved_as_custom(self):
        for value in ('${saved_entry}', 'missing', '1>0', 'uefi-firmware'):
            self.assertIsNone(helper.boot_systems(MENU.replace('default="0"', f'default="{value}"'))['current'])
        self.assertIsNone(helper.boot_systems(MENU + '\nset default="2"\n')['current'])

    def test_escaped_titles_and_braces_do_not_break_discovery(self):
        menu = MENU.replace("'AnduinOS'", "'Someone'\\''s OS {test}'")
        self.assertEqual(helper.boot_systems(menu)['entries'][0]['title'], "Someone's OS {test}")

    def test_duplicate_and_unsafe_ids_are_not_offered(self):
        duplicate = MENU.replace('osprober-gnulinux-simple-def', WINDOWS)
        self.assertNotIn(WINDOWS, [e['id'] for e in helper.boot_systems(duplicate)['entries']])
        for value in ('osprober-efi-$(touch /tmp/no)', 'osprober-efi-x;reboot', 'osprober-efi-x>y'):
            self.assertNotIn(value, [e['id'] for e in helper.boot_systems(MENU.replace(WINDOWS, value))['entries']])

    def test_malformed_menu_fails_closed(self):
        with self.assertRaises(ValueError):
            helper.boot_systems(MENU + '\nmenuentry "unterminated')
        with self.assertRaises(ValueError):
            helper.boot_systems(MENU + '\nsubmenu x {')

    def test_list_action_returns_only_json_and_does_not_update_grub(self):
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper, 'read_boot_systems', return_value=helper.boot_systems(MENU)), patch.object(helper, 'update_grub') as update:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(helper.main(['list-systems']), 0)
            self.assertEqual(json.loads(output.getvalue())['current'], 'gnulinux-simple-abc')
            update.assert_not_called()

    def test_writes_default_and_checks_regenerated_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.cfg'
            menu = Path(directory) / 'grub.cfg'
            menu.write_text(MENU)
            config.write_text("GRUB_DEFAULT=saved\nGRUB_CMDLINE_LINUX='quiet'\n")
            def regenerate():
                menu.write_text(MENU.replace('default="0"', f'default="{WINDOWS}"'))
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'GRUB_MENU', menu), patch.object(helper, 'update_grub', side_effect=regenerate):
                helper.set_settings(10, 'native', WINDOWS)
                self.assertIn(f'GRUB_DEFAULT={WINDOWS}\n', config.read_text())
                self.assertIn("GRUB_CMDLINE_LINUX='quiet'", config.read_text())
                helper.set_timeout(5)
                self.assertIn(f'GRUB_DEFAULT={WINDOWS}\n', config.read_text())

    def test_unknown_and_injected_selection_never_write(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.cfg'
            config.write_text('original\n')
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'read_boot_systems', return_value=helper.boot_systems(MENU)), patch.object(helper, 'update_grub') as update:
                for value in ('missing', WINDOWS + '; reboot', '', 'uefi-firmware'):
                    with self.assertRaises(ValueError):
                        helper.set_settings(10, 'native', value)
                    self.assertEqual(config.read_text(), 'original\n')
                update.assert_not_called()

    def test_regeneration_losing_selection_restores_config_and_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.cfg'
            config.write_text('original\n')
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'read_boot_systems', return_value=helper.boot_systems(MENU)), patch.object(helper, 'update_grub') as update:
                with self.assertRaises(RuntimeError):
                    helper.set_settings(10, 'native', WINDOWS)
                self.assertEqual(config.read_text(), 'original\n')
                self.assertEqual(update.call_count, 2)

    def test_refresh_failure_removes_new_config_and_restores_generated_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'new.cfg'
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'read_boot_systems', return_value=helper.boot_systems(MENU)), patch.object(helper, 'update_grub', side_effect=[RuntimeError('failed'), None]) as update:
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    helper.set_settings(10, 'native', WINDOWS)
                self.assertFalse(config.exists())
                self.assertEqual(update.call_count, 2)

    def test_preserves_custom_default_verbatim_without_reading_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.cfg'
            custom = 'export GRUB_DEFAULT="${CUSTOM_DEFAULT}" # keep\n'
            config.write_text(custom + 'GRUB_TIMEOUT=3\n')
            with patch.object(helper, 'CONFIGURATION_PATH', config), patch.object(helper, 'read_boot_systems') as read, patch.object(helper, 'update_grub'):
                helper.set_settings(10, 'native')
                self.assertIn(custom, config.read_text())
                self.assertNotIn('GRUB_TIMEOUT=3\n', config.read_text())
                read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
