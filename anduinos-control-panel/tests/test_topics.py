from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_control_panel.topics import (  # noqa: E402
    search_topics,
    topics,
)


class TopicCatalogTests(unittest.TestCase):
    def test_topic_ids_are_unique(self):
        identifiers = [topic.identifier for topic in topics()]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_shell_queries_find_expected_control_panel_topics(self):
        expected = {
            "swap": "system.virtual-memory",
            "firewall": "network.firewall",
            "yubikey": "accounts.yubikey",
            "grub": "system.startup-boot",
            "btrfs": "recovery.snapshots",
            "scanner": "hardware.scanners",
            "扫描仪": "hardware.scanners",
            "高级网络": "network.advanced",
        }
        for query, identifier in expected.items():
            with self.subTest(query=query):
                results = search_topics([query])
                self.assertTrue(results)
                self.assertEqual(results[0].identifier, identifier)

    def test_multi_term_search_requires_every_term(self):
        results = search_topics(["secure", "boot"])
        self.assertEqual(results[0].identifier, "security.secure-boot")
        self.assertNotIn(
            "system.startup-boot", [topic.identifier for topic in results]
        )

    def test_subsearch_respects_previous_results(self):
        results = search_topics(
            ["zswap"], candidates=["network.firewall", "system.virtual-memory"]
        )
        self.assertEqual(
            [topic.identifier for topic in results], ["system.virtual-memory"]
        )


if __name__ == "__main__":
    unittest.main()
