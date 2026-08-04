import tempfile
import unittest
from pathlib import Path

from installer_core.swap_policy import (
    GIB,
    SwapSizingError,
    calculate_swap_sizing,
    probe_physical_memory_bytes,
)


class SwapSizingTableTests(unittest.TestCase):
    def test_policy_table(self):
        # RAM GiB, installation-space GiB, budget GiB, runtime target GiB,
        # hibernation target GiB, chosen swap GiB, hibernation capacity.
        cases = (
            (0.5, 24, 2, 2, 2, 2, True),
            (3, 24, 2, 2, 4, 2, False),
            (4, 30, 8, 2, 5, 5, True),
            (8, 30, 8, 4, 9, 4, False),
            (8, 45, 23, 4, 9, 9, True),
            (16, 45, 23, 8, 17, 17, True),
            (32, 45, 23, 16, 33, 16, False),
            (64, 100, 78, 32, 65, 65, True),
            (100, 100, 78, 50, 101, 50, False),
            (128, 100, 78, 64, 129, 64, False),
            (128, 151, 129, 64, 129, 129, True),
            (256, 200, 178, 64, 257, 64, False),
            (256, 300, 278, 64, 257, 257, True),
        )
        for (
            ram_gib,
            space_gib,
            budget_gib,
            runtime_gib,
            hibernation_gib,
            swap_gib,
            hibernation_capacity,
        ) in cases:
            with self.subTest(ram_gib=ram_gib, space_gib=space_gib):
                result = calculate_swap_sizing(
                    int(ram_gib * GIB),
                    space_gib * GIB,
                )
                self.assertEqual(result.disk_budget_mib, budget_gib * 1024)
                self.assertEqual(
                    result.runtime_target_mib, runtime_gib * 1024
                )
                self.assertEqual(
                    result.hibernation_target_mib, hibernation_gib * 1024
                )
                self.assertEqual(result.swap_size_mib, swap_gib * 1024)
                self.assertEqual(
                    result.hibernation_capacity, hibernation_capacity
                )

    def test_rejects_space_that_cannot_preserve_minimum_root_and_swap(self):
        with self.assertRaisesRegex(SwapSizingError, "provide 2 GiB swap"):
            calculate_swap_sizing(8 * GIB, 23 * GIB)

    def test_reused_esp_increases_the_guided_space_budget(self):
        result = calculate_swap_sizing(
            8 * GIB,
            23 * GIB,
            esp_size_mib=0,
        )
        self.assertEqual(result.swap_size_mib, 2 * 1024)


class PhysicalMemoryProbeTests(unittest.TestCase):
    def test_reads_memtotal_kib(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text(
                "MemTotal:       8388608 kB\nMemFree: 1 kB\n",
                encoding="ascii",
            )
            self.assertEqual(probe_physical_memory_bytes(path), 8 * GIB)

    def test_rejects_missing_or_malformed_memtotal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text("MemTotal: unknown kB\n", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "parse MemTotal"):
                probe_physical_memory_bytes(path)


if __name__ == "__main__":
    unittest.main()
