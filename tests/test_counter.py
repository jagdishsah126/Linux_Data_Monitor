import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from counter import read_interfaces, read_totals, format_bytes  # noqa: E402

SAMPLE = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1024      12    0    0    0     0          0         0     1024      12    0    0    0     0       0          0
  eth0:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0
 wlan0: 7000000   12716    0    0    0     0          0         0  5000000    9634    0    0    0     0       0          0
"""


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dev"
        self.path.write_text(SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loopback_is_excluded(self):
        ifaces = read_interfaces(self.path)
        self.assertNotIn("lo", ifaces)
        self.assertEqual(set(ifaces), {"eth0", "wlan0"})

    def test_values_parsed(self):
        ifaces = read_interfaces(self.path)
        self.assertEqual(ifaces["wlan0"], (7000000, 5000000))
        self.assertEqual(ifaces["eth0"], (0, 0))

    def test_totals_sum_interfaces(self):
        rx, tx = read_totals(self.path)
        self.assertEqual(rx, 7000000)
        self.assertEqual(tx, 5000000)

    def test_missing_file_is_safe(self):
        self.assertEqual(read_totals(Path(self.tmp.name) / "nope"), (0, 0))
        self.assertEqual(read_interfaces(Path(self.tmp.name) / "nope"), {})

    def test_format_bytes(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2.0 KB")
        self.assertEqual(format_bytes(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(format_bytes(3 * 1024 ** 3), "3.00 GB")


if __name__ == "__main__":
    unittest.main()
