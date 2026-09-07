#!/usr/bin/env python3
from pathlib import Path
import re
import runpy
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "anduinos-apt-config": "packages.anduinos.com",
    "anduinos-apt-config-dev": "apkg-dev.aiursoft.com",
}
POLICY_NAME = "52anduinos-unattended-upgrades"


class UnattendedUpgradesPolicyTests(unittest.TestCase):
    def test_exact_default_rules(self):
        for package, host in PACKAGES.items():
            with self.subTest(package=package):
                policy = (ROOT / package / "assets" / POLICY_NAME).read_text()
                self.assertEqual(re.findall(r'^\s*"([^"]+)";', policy, re.M), [
                    'o=Ubuntu,a=${distro_codename}',
                    'o=Ubuntu,a=${distro_codename}-security',
                    'o=Aiursoft Apkg,a=${distro_codename}-addon,site=' + host,
                    'o=Aiursoft Apkg,a=${distro_codename}-webapps,site=' + host,
                ])
                self.assertNotIn('#clear', policy)

    def test_packages_ship_their_own_policy_and_build_gate(self):
        for package, host in PACKAGES.items():
            project = ET.parse(ROOT / package / (package + '.aosproj'))
            policies = [f for f in project.findall('.//IncludeFile')
                        if f.get('Target') == '/etc/apt/apt.conf.d/' + POLICY_NAME]
            self.assertEqual(len(policies), 1)
            self.assertEqual(policies[0].get('Include'), 'assets/' + POLICY_NAME)
            commands = [c.get('Run', '') for c in project.findall('.//PrebuildCommand')]
            self.assertTrue(any('test_unattended_upgrades_policy.py' in c for c in commands))
            for source in (ROOT / package / 'assets').glob('*/*/anduinos.sources'):
                self.assertIn('https://' + host + '/', source.read_text())


class ActualMatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path('/usr/bin/unattended-upgrade').exists():
            raise unittest.SkipTest('Install unattended-upgrades for integration tests')
        try:
            import apt_pkg
        except ImportError:
            raise unittest.SkipTest('Use system Python with python3-apt for integration tests')
        cls.apt_pkg = apt_pkg
        # Import only: never execute main(), install packages or write system config.
        cls.module = runpy.run_path('/usr/bin/unattended-upgrade', run_name='policy_test')

    def test_real_config_parser_and_origin_matching(self):
        config = self.apt_pkg.config
        for package, host in PACKAGES.items():
            for codename in ('noble', 'resolute'):
                with self.subTest(package=package, codename=codename):
                    config.clear('Unattended-Upgrade')
                    policy = str(ROOT / package / 'assets' / POLICY_NAME)
                    self.apt_pkg.read_config_file(config, policy)
                    namespace = self.module['substitute'].__globals__
                    namespace['DISTRO_ID'] = 'AnduinOS'
                    namespace['DISTRO_CODENAME'] = codename
                    rules = self.module['get_allowed_origins']()
                    self.assertEqual(len(rules), 4)

                    def allowed(origin, suite, site=host):
                        record = SimpleNamespace(origin=origin, archive=suite,
                                                 site=site, label=origin,
                                                 component='main', codename=suite)
                        return self.module['is_allowed_origin'](record, rules)

                    self.assertTrue(allowed('Ubuntu', codename, 'archive.ubuntu.com'))
                    self.assertTrue(allowed('Ubuntu', codename + '-security', 'security.ubuntu.com'))
                    for suffix in ('-addon', '-webapps'):
                        suite = codename + suffix
                        self.assertTrue(allowed('Aiursoft Apkg', suite))
                        foreign_hosts = ['third-party.example'] + [h for h in PACKAGES.values() if h != host]
                        for foreign in foreign_hosts:
                            self.assertFalse(allowed('Aiursoft Apkg', suite, foreign))
                        self.assertFalse(allowed('Unrelated vendor', suite))
                    for suffix in ('-updates', '-backports', '-proposed'):
                        self.assertFalse(allowed('Ubuntu', codename + suffix))
                    self.assertFalse(allowed('Aiursoft Apkg', 'other-addon'))
                    self.assertFalse(allowed('Aiursoft Apkg', codename + '-testing'))

                    # Local administrator policy remains additive, not erased.
                    config.set('Unattended-Upgrade::Allowed-Origins::', 'LocalAdmin:custom')
                    self.apt_pkg.read_config_file(config, policy)
                    self.assertIn('o=LocalAdmin,a=custom', self.module['get_allowed_origins']())


if __name__ == "__main__":
    unittest.main()
