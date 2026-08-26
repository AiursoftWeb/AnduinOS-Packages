from pathlib import Path
import unittest


class PackagingTests(unittest.TestCase):
    def test_window_opens_in_the_wide_security_overview_layout(self):
        source = Path("src/window.rs").read_text(encoding="utf-8")
        self.assertIn('.property("default-width", 1266)', source)
        self.assertIn('.property("default-height", 795)', source)
        self.assertNotIn('.property("default-width", 900)', source)
        self.assertNotIn('.property("default-height", 650)', source)

    def test_prerm_exits_before_cleanup_during_upgrade(self):
        script = Path("scripts/prerm.sh").read_text(encoding="utf-8")
        guard = script.index('case "${1:-}" in')
        upgrade = script.index("upgrade|failed-upgrade|*")
        cleanup = script.index("python3 <<'PY'")
        self.assertLess(guard, upgrade)
        self.assertLess(upgrade, cleanup)
        self.assertIn("exit 0", script[upgrade:cleanup])

    def test_postinst_reconciles_persistent_authentication_state(self):
        script = Path("scripts/postinst.sh").read_text(encoding="utf-8")
        self.assertIn("/usr/lib/anduinos-yubikey-manager/helper repair", script)
        self.assertIn('"configure"', script)

    def test_removal_preserves_shared_passwordless_sudo_policy(self):
        script = Path("scripts/prerm.sh").read_text(encoding="utf-8")
        self.assertIn("90-anduinos-yubikey-manager", script)
        self.assertNotIn(
            'managed_sudoers = "/etc/sudoers.d/90-anduinos-passwordless-admin"',
            script,
        )


if __name__ == "__main__":
    unittest.main()
