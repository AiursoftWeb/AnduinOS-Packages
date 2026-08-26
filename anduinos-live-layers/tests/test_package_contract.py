#!/usr/bin/env python3

from pathlib import Path
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
PROJECT = PACKAGE / "anduinos-live-layers.aosproj"
MODULE = PACKAGE / "dracut/95anduinos-live-layers/module-setup.sh"
PREPARE = PACKAGE / "dracut/95anduinos-live-layers/anduinos-live-prepare.sh"
CREATE_OVERLAY = (
    PACKAGE / "dracut/95anduinos-live-layers/anduinos-create-overlay.sh"
)


class LiveLayersPackageContractTests(unittest.TestCase):
    def test_package_is_dracut_only(self):
        project = PROJECT.read_text(encoding="utf-8")
        self.assertIn('<Dependency Include="dracut" />', project)
        self.assertIn('<Dependency Include="dracut-core" />', project)
        self.assertIn("initramfs-tools-core", project)
        self.assertIn("casper", project)
        self.assertNotIn("/usr/share/initramfs-tools", project)

    def test_module_composes_upstream_live_modules(self):
        module = MODULE.read_text(encoding="utf-8")
        for dependency in (
            "dmsquash-live",
            "dmsquash-live-autooverlay",
            "overlayfs",
        ):
            self.assertIn(dependency, module)
        self.assertIn("inst_hook pre-pivot 90", module)
        self.assertIn("create-overlay.upstream", module)
        self.assertIn('rm -f "$initdir/sbin/create-overlay"', module)
        self.assertIn('"/sbin/create-overlay"', module)
        self.assertIn("[[ $hostonly ]] && return 1", module)

    def test_auto_overlay_wrapper_repairs_expanded_gpt_only_for_our_abi(self):
        wrapper = CREATE_OVERLAY.read_text(encoding="utf-8")
        self.assertIn("getargbool 0 rd.anduinos.live", wrapper)
        self.assertIn('LABEL=ANDUINOS-PERSIST', wrapper)
        self.assertLessEqual(len("ANDUINOS-PERSIST".encode("ascii")), 16)
        self.assertIn('partition_table" = gpt', wrapper)
        self.assertIn('parted --script --fix "$block_device" print', wrapper)
        self.assertIn('exec /sbin/create-overlay.upstream "$@"', wrapper)

    def test_pre_pivot_contract_exposes_media_source_and_marker(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        self.assertIn("getargbool 0 rd.anduinos.live", prepare)
        self.assertIn('mount --bind "$media_root" "$NEWROOT/cdrom"', prepare)
        self.assertIn("runtime_root=/run/anduinos-live", prepare)
        self.assertNotIn('$NEWROOT/run/anduinos-live', prepare)
        self.assertIn("/run/anduinos-live/rootfs.squashfs", prepare)
        self.assertIn('cat > "$runtime_root/environment"', prepare)
        self.assertIn("ANDUINOS_LIVE=1", prepare)
        self.assertIn("Invalid AnduinOS Live directory", prepare)
        self.assertIn("Invalid AnduinOS Live image name", prepare)


if __name__ == "__main__":
    unittest.main()
