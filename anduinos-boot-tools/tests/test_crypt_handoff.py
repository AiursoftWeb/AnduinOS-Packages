"""Build selection and metadata behavior for installer-managed encrypted roots."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "dracut/92anduinos-crypt/module-setup.sh"
UUID = "b872c010-0f0c-4fae-954b-d4c1e7644889"
ENTRY = f"anduinos-root UUID={UUID} none luks,x-initrd.attach\n"


class CryptHandoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.image = self.root / "image"
        (self.source / "etc").mkdir(parents=True)
        (self.image / "etc").mkdir(parents=True)
        self.environment = dict(
            os.environ,
            dracutsysrootdir=str(self.source),
            initdir=str(self.image),
            moddir=str(MODULE.parent),
        )

    def run_module(self, action):
        # Dracut supplies these build functions. Exercise the module against
        # temporary source/image trees without requiring a kernel or root.
        return subprocess.run(
            ["bash", "-c", '''
                . "$1"
                require_binaries() { return 0; }
                inst_multiple() { :; }
                inst_hook() { :; }
                "$2"
            ''', "module-test", str(MODULE), action],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def crypttab(self, text):
        (self.source / "etc/crypttab").write_text(text)

    def test_plain_or_unrelated_encryption_does_not_select_handoff(self):
        self.assertNotEqual(self.run_module("check").returncode, 0)
        self.crypttab(f"other-root UUID={UUID} none luks\n")
        self.assertNotEqual(self.run_module("check").returncode, 0)
        self.assertFalse((self.image / "etc/crypttab").exists())

    def test_reserved_root_selects_handoff_for_generic_image(self):
        self.crypttab(ENTRY)
        self.assertEqual(self.run_module("check").returncode, 0)
        self.assertEqual(self.run_module("install").returncode, 0)
        self.assertEqual((self.image / "etc/crypttab").read_text(), ENTRY)

    def test_duplicate_or_nonstandard_root_is_not_silently_accepted(self):
        for entry in (
            ENTRY + ENTRY,
            ENTRY.replace(UUID, "not-a-luks-uuid"),
            ENTRY.replace(" none ", " /etc/custom.key "),
            ENTRY.replace(",x-initrd.attach", ""),
            ENTRY.replace(" luks,", " plain,"),
        ):
            with self.subTest(entry=entry):
                self.crypttab(entry)
                self.assertNotEqual(self.run_module("check").returncode, 0)
                self.assertNotEqual(self.run_module("install").returncode, 0)

    def test_hostonly_entries_survive_and_root_is_not_duplicated(self):
        self.crypttab(ENTRY)
        other = f"data UUID={UUID} none luks\n"
        output = self.image / "etc/crypttab"
        output.write_text(other)
        for _ in range(2):
            self.assertEqual(self.run_module("install").returncode, 0)
        self.assertEqual(output.read_text(), other + ENTRY)

    def test_build_does_not_copy_machine_or_transient_key(self):
        self.crypttab(ENTRY)
        private = self.source / "etc/anduinos-luks"
        private.mkdir()
        (private / "machine.key").write_bytes(os.urandom(64))
        (self.source / "anduinos-unlock.key").write_bytes(os.urandom(64))
        self.assertEqual(self.run_module("install").returncode, 0)
        self.assertFalse((self.image / "etc/anduinos-luks/machine.key").exists())
        self.assertFalse((self.image / "anduinos-unlock.key").exists())


if __name__ == "__main__":
    unittest.main()
