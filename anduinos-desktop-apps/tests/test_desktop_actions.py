"""Application renaming must not overwrite upstream desktop action names."""
import configparser
from pathlib import Path
import unittest


class DesktopActionTests(unittest.TestCase):
    def test_celluloid_actions_remain_distinct_in_every_available_language(self):
        desktop = Path(__file__).resolve().parents[1] / 'assets/io.github.celluloid_player.Celluloid.desktop'
        config = configparser.ConfigParser(interpolation=None, strict=True)
        config.optionxform = str
        config.read(desktop, encoding='utf-8')
        main = config['Desktop Entry']
        for action in filter(None, main['Actions'].split(';')):
            group = config['Desktop Action ' + action]
            self.assertTrue(group['Name'].strip())
            self.assertNotEqual(group['Name'], main['Name'])
            for key, value in group.items():
                if key.startswith('Name['):
                    with self.subTest(action=action, locale=key):
                        self.assertTrue(value.strip())
                        self.assertNotEqual(value, main.get(key, main['Name']))


if __name__ == '__main__':
    unittest.main()
