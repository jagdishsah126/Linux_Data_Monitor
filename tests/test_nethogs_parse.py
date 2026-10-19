import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from nethogs_parse import parse_trace_line, normalize_app, TraceAccumulator  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_tab_separated_line(self):
        app, pid, uid, sent, recv = parse_trace_line("brave/1234/1000\t2048\t8192")
        self.assertEqual(app, "brave")
        self.assertEqual((pid, uid), (1234, 1000))
        self.assertEqual((sent, recv), (2048, 8192))

    def test_full_path_name_keeps_basename(self):
        app, *_ = parse_trace_line("/usr/lib/firefox/firefox/55/1000\t1\t2")
        self.assertEqual(app, "firefox")

    def test_unknown_is_grouped(self):
        app, *_ = parse_trace_line("unknown TCP/0/0\t1\t2")
        self.assertEqual(app, "Unknown")

    def test_headers_and_blank_ignored(self):
        self.assertIsNone(parse_trace_line("Refreshing:"))
        self.assertIsNone(parse_trace_line(""))
        self.assertIsNone(parse_trace_line("garbage"))

    def test_float_values_are_accepted(self):
        app, _pid, _uid, sent, recv = parse_trace_line("brave/1/1000\t1.5\t2.5")
        self.assertEqual((sent, recv), (1, 2))

    def test_normalize_app(self):
        self.assertEqual(normalize_app("unknown udp"), "Unknown")
        self.assertEqual(normalize_app(""), "Unknown")
        self.assertEqual(normalize_app("brave"), "brave")

    def test_connection_tuple_becomes_unknown(self):
        app, pid, uid, sent, recv = parse_trace_line(
            "10.0.0.5:443-10.0.0.9:52133\t339\t503"
        )
        self.assertEqual(app, "Unknown")
        self.assertIsNone(pid)
        self.assertIsNone(uid)
        self.assertEqual((sent, recv), (339, 503))

    def test_real_nethogs_header_lines_ignored(self):
        self.assertIsNone(parse_trace_line("Adding local address: 10.0.0.5"))
        self.assertIsNone(parse_trace_line("Ethernet link detected"))


class AccumulatorTests(unittest.TestCase):
    def setUp(self):
        self.acc = TraceAccumulator()

    def test_first_feed_is_full_value(self):
        self.assertEqual(self.acc.feed("brave", 100, 500), (100, 500))

    def test_deltas_between_refreshes(self):
        self.acc.feed("brave", 100, 500)
        self.assertEqual(self.acc.feed("brave", 180, 900), (80, 400))

    def test_reset_detected(self):
        self.acc.feed("brave", 900, 900)
        self.assertEqual(self.acc.feed("brave", 10, 20), (10, 20))

    def test_apps_tracked_independently(self):
        self.acc.feed("a", 10, 10)
        self.acc.feed("b", 5, 5)
        self.assertEqual(self.acc.feed("a", 30, 30), (20, 20))
        self.assertEqual(self.acc.feed("b", 15, 15), (10, 10))


if __name__ == "__main__":
    unittest.main()
