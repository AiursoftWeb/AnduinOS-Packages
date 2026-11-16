"""Exercise real resource generation and alternatives in a disposable filesystem."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

PACKAGE = Path(__file__).resolve().parents[1]
RESOURCE = '/org/gnome/shell/theme/background.png'
VERSION = ET.parse(PACKAGE / 'anduinos-gdm3-wallpaper.aosproj').findtext('.//PackageVersion').replace('$(SuiteShortName)', 'resolute-addon')


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        for command in ('gresource', 'glib-compile-resources', 'update-alternatives'):
            self.assertIsNotNone(shutil.which(command), f'Missing prerequisite: {command}')
        self.temp = tempfile.TemporaryDirectory(prefix='gdm wallpaper ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'var/lib/anduinos-gdm3-wallpaper'
        self.state.mkdir(parents=True)
        self.share = self.root / 'usr/share'
        self.default = self.share / 'anduinos-gdm3-wallpaper/Floating-objects-dark.png'
        self.default.parent.mkdir(parents=True)
        self.default.write_bytes(b'packaged background')
        self.output = self.state / 'anduinos-theme.gresource'
        self.saved = self.state / 'wallpaper.png'
        self.base = self.share / 'gnome-shell/gnome-shell-theme.gresource'
        self.base.parent.mkdir(parents=True)
        self.make_resource(self.base, b'upstream background')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}')
        self.altdir = self.root / 'alternatives'
        self.admindir = self.root / 'admin'
        self.altdir.mkdir()
        self.admindir.mkdir()
        # Redirect the privileged boundary; use the real alternatives implementation.
        self.command('update-alternatives',
                     f'exec /usr/bin/update-alternatives --altdir "{self.altdir}" '
                     f'--admindir "{self.admindir}" "$@"')
        generator = (PACKAGE / 'scripts/anduinos-gdm-set-wallpaper').read_text()
        generator = self.redirect(generator).replace('  require_root\n', '  : # privilege checked outside this isolated test\n')
        self.engine = self.bin / 'anduinos-gdm-set-wallpaper'
        self.engine.write_text(generator)
        self.engine.chmod(0o755)
        self.postinst = self.root / 'postinst'
        text = self.redirect((PACKAGE / 'scripts/postinst.sh').read_text())
        self.postinst.write_text(text.replace('/usr/bin/anduinos-gdm-set-wallpaper', f'"{self.engine}"'))

    def redirect(self, text):
        return text.replace('/var/lib/anduinos-gdm3-wallpaper', str(self.state)).replace('/usr/share/', str(self.share) + '/')

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def run_command(self, *args, check=True):
        result = subprocess.run(args, env=self.env, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def make_resource(self, path, background):
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            source = Path(td)
            (source / 'background.png').write_bytes(background)
            (source / 'gnome-shell.css').write_text('/* upstream theme */')
            manifest = source / 'theme.xml'
            manifest.write_text('<gresources><gresource prefix="/org/gnome/shell/theme"><file>background.png</file><file>gnome-shell.css</file></gresource></gresources>')
            subprocess.run(['glib-compile-resources', f'--sourcedir={source}', f'--target={path}', str(manifest)], check=True, capture_output=True)

    def background(self):
        return subprocess.check_output(['gresource', 'extract', str(self.output), RESOURCE])

    def configure(self, expect_warning=False, previous_version=VERSION):
        result = self.run_command('sh', str(self.postinst), 'configure', previous_version)
        if not expect_warning:
            self.assertNotIn('Warning: Cannot', result.stdout)

    def generate(self, source, output=None):
        return self.run_command(str(self.engine), '--wallpaper', str(source), '--output', str(output or self.output), check=False)

    def test_first_install_and_default_updates(self):
        self.configure()
        self.assertEqual(self.background(), self.default.read_bytes())
        self.assertFalse(self.saved.exists())
        self.default.write_bytes(b'updated packaged background')
        self.configure()
        self.assertEqual(self.background(), b'updated packaged background')
        self.assertFalse(self.saved.exists())

    def test_old_custom_background_migrates_and_survives_repeated_upgrades(self):
        self.make_resource(self.output, b'old customized image with effects')
        self.configure(previous_version='2.0.5-1+resolute-addon')
        self.assertEqual(self.saved.read_bytes(), b'old customized image with effects')
        self.default.write_bytes(b'new distribution image')
        self.configure()
        self.assertEqual(self.background(), b'old customized image with effects')

    def test_new_selection_survives_deleted_original(self):
        source = self.root / 'my background.jpg'
        source.write_bytes(b'new custom image')
        self.assertEqual(self.generate(source).returncode, 0)
        source.unlink()
        self.configure()
        self.assertEqual(self.background(), b'new custom image')
        self.assertEqual(self.saved.read_bytes(), b'new custom image')

    def test_export_does_not_change_installed_selection(self):
        self.configure()
        source = self.root / 'export.png'
        source.write_bytes(b'export only')
        self.assertEqual(self.generate(source, self.root / 'export.gresource').returncode, 0)
        self.assertFalse(self.saved.exists())
        self.assertEqual(self.background(), self.default.read_bytes())

    def test_failed_compile_preserves_theme_and_saved_image(self):
        source = self.root / 'custom.png'
        source.write_bytes(b'working image')
        self.assertEqual(self.generate(source).returncode, 0)
        original = self.output.read_bytes()
        source.write_bytes(b'replacement image')
        self.command('glib-compile-resources', 'exit 1')
        self.assertNotEqual(self.generate(source).returncode, 0)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(self.saved.read_bytes(), b'working image')
        self.configure(expect_warning=True)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(self.saved.read_bytes(), b'working image')
        self.assertFalse(list(self.state.glob('.anduinos-gdm-*')))

    def test_unreadable_old_resource_is_not_overwritten(self):
        self.output.write_bytes(b'damaged resource')
        self.configure(expect_warning=True, previous_version='2.1.0-2+resolute-addon')
        self.assertEqual(self.output.read_bytes(), b'damaged resource')
        self.assertFalse(self.saved.exists())

    def test_other_theme_selection_is_preserved_in_auto_and_manual_modes(self):
        other = self.root / 'other.gresource'
        self.make_resource(other, b'other theme')
        link = self.share / 'gnome-shell/gdm-theme.gresource'
        for mode in ('--auto', '--set'):
            with self.subTest(mode=mode):
                self.run_command('update-alternatives', '--install', str(link), 'gdm-theme.gresource', str(other), '10')
                args = ['update-alternatives', mode, 'gdm-theme.gresource']
                if mode == '--set':
                    args.append(str(other))
                else:
                    self.run_command('update-alternatives', '--remove', 'gdm-theme.gresource', str(self.output), check=False)
                self.run_command(*args)
                self.configure()
                self.assertEqual(link.resolve(), other)

    def test_non_configure_actions_do_nothing(self):
        self.run_command('sh', str(self.postinst), 'abort-upgrade')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
