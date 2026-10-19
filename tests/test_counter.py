import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from unittest import mock

from counter import (  # noqa: E402
    DEFAULT_EXCLUDE,
    exclude_patterns,
    format_bytes,
    read_interfaces,
    read_totals,
)

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


class VirtualInterfaceTests(unittest.TestCase):
    """Container/bridge interfaces must not be counted twice.

    ``/proc/net/dev`` is per-interface, so a container download shows up on the
    veth pair, on docker0 *and* on the physical interface: three counters, one
    download.  The virtual devices always sit in front of a counted physical
    one, so counting them too is pure double-counting.
    """

    SAMPLE = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1024      12    0    0    0     0          0         0     1024      12    0    0    0     0       0          0
  eth0: 1000000       0    0    0    0     0          0         0   500000       0    0    0    0     0       0          0
docker0:  400000       0    0    0    0     0          0         0   200000       0    0    0    0     0       0          0
 veth9f:  400000       0    0    0    0     0          0         0   200000       0    0    0    0     0       0          0
 virbr0:  100000       0    0    0    0     0          0         0    50000       0    0    0    0     0       0          0
  br-1a:  100000       0    0    0    0     0          0         0    50000       0    0    0    0     0       0          0
  tun0:  300000       0    0    0    0     0          0         0   100000       0    0    0    0     0       0          0
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dev"
        self.path.write_text(self.SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_virtual_interfaces_are_excluded(self):
        self.assertEqual(set(read_interfaces(self.path)), {"eth0", "tun0"})

    def test_totals_count_container_traffic_once(self):
        # eth0 + tun0 only.  Counting docker0/veth/virbr/br-1 as well would
        # report 2.4 MB down for the same 1.0 MB.
        self.assertEqual(read_totals(self.path), (1300000, 600000))

    def test_exclusion_can_be_disabled(self):
        self.assertEqual(
            set(read_interfaces(self.path, exclude="")),
            {"eth0", "docker0", "veth9f", "virbr0", "br-1a", "tun0"},
        )

    def test_exclusion_can_be_overridden(self):
        self.assertEqual(
            set(read_interfaces(self.path, exclude="eth*,tun0")),
            {"docker0", "veth9f", "virbr0", "br-1a"},
        )

    def test_env_override_precedence(self):
        with mock.patch.dict(os.environ, {"DATA_MONITOR_EXCLUDE": "eth*"}):
            self.assertEqual(exclude_patterns(), ("eth*",))
        with mock.patch.dict(os.environ, {"DATA_MONITOR_EXCLUDE": "  "}):
            self.assertEqual(exclude_patterns(), ())       # explicit: count all
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(exclude_patterns(), DEFAULT_EXCLUDE)


if __name__ == "__main__":
    unittest.main()
