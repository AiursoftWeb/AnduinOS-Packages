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
        self.assertEqual(main['Name'], 'Multimedia Player')
        self.assertEqual(main['Actions'], 'new-window;enqueue;')
        for action, name, command in (
                ('new-window', 'New Window', 'celluloid --new-window'),
                ('enqueue', 'Add to Playlist', 'celluloid --enqueue %U')):
            group = config['Desktop Action ' + action]
            self.assertEqual(group['Name'], name)
            self.assertEqual(group['Exec'], command)
            self.assertIn('Name[zh_CN]', group)
            self.assertIn('Name[de]', group)
            for key, value in group.items():
                if key.startswith('Name['):
                    with self.subTest(action=action, locale=key):
                        self.assertTrue(value.strip())
                        self.assertNotEqual(value, main.get(key, main['Name']))


if __name__ == '__main__':
    unittest.main()
