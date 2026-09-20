#!/usr/bin/env python3
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"anduinos-apt-config-dev": "apkg-dev.aiursoft.com"}
POLICY_NAME = "52anduinos-unattended-upgrades"


class ActualMatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path('/usr/bin/unattended-upgrade').exists():
            raise RuntimeError('Install unattended-upgrades for integration tests')
        try:
            import apt_pkg
        except ImportError:
            raise RuntimeError('Use system Python with python3-apt for integration tests')
        cls.apt_pkg = apt_pkg
        # Import only: never execute main(), install packages or write system config.
        cls.module = runpy.run_path('/usr/bin/unattended-upgrade', run_name='policy_test')

    def test_real_config_parser_and_origin_matching(self):
        config = self.apt_pkg.config
        for package, host in PACKAGES.items():
            for codename in ('noble', 'resolute'):
                with self.subTest(package=package, codename=codename):
                    config.clear('Unattended-Upgrade')
                    policy = str(ROOT / 'assets' / POLICY_NAME)
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
                        foreign_hosts = ['third-party.example', 'packages.anduinos.com']
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
