import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from nethogs_parse import parse_trace_line, normalize_app, TraceAccumulator  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_tab_separated_line(self):
        e = parse_trace_line("brave/1234/1000\t2048\t8192")
        self.assertEqual(e.app, "brave")
        self.assertEqual((e.pid, e.uid), (1234, 1000))
        self.assertEqual((e.sent, e.recv), (2048, 8192))
        # the key is the label as nethogs printed it, pid and uid included:
        # that is what the cumulative counter belongs to.
        self.assertEqual(e.key, "brave/1234/1000")

    def test_full_path_name_keeps_basename(self):
        e = parse_trace_line("/usr/lib/firefox/firefox/55/1000\t1\t2")
        self.assertEqual(e.app, "firefox")
        # the *label* keeps the path; only the display name is trimmed
        self.assertEqual(e.key, "/usr/lib/firefox/firefox/55/1000")

    def test_unknown_is_grouped(self):
        e = parse_trace_line("unknown TCP/0/0\t1\t2")
        self.assertEqual(e.app, "Unknown")

    def test_headers_and_blank_ignored(self):
        self.assertIsNone(parse_trace_line("Refreshing:"))
        self.assertIsNone(parse_trace_line(""))
        self.assertIsNone(parse_trace_line("garbage"))

    def test_float_values_are_accepted(self):
        e = parse_trace_line("brave/1/1000\t1.5\t2.5")
        self.assertEqual((e.sent, e.recv), (1, 2))

    def test_normalize_app(self):
        self.assertEqual(normalize_app("unknown udp"), "Unknown")
        self.assertEqual(normalize_app(""), "Unknown")
        self.assertEqual(normalize_app("brave"), "brave")

    def test_normalize_app_strips_command_line_arguments(self):
        # nethogs prints the whole argv for some processes; only the executable
        # should survive, or the tooltip fills with a huge blob.
        self.assertEqual(
            normalize_app("/opt/brave.com/brave/brave --type=utility --lang=en-US"),
            "brave",
        )
        self.assertEqual(
            normalize_app("python3 /home/user/script.py --flag"), "python3"
        )

    def test_full_command_line_becomes_the_executable(self):
        e = parse_trace_line(
            "/opt/brave.com/brave/brave --type=utility "
            "--utility-sub-type=network.mojom.NetworkService/4013/1000\t4886\t31798"
        )
        self.assertEqual(e.app, "brave")
        self.assertEqual((e.pid, e.uid), (4013, 1000))
        self.assertEqual((e.sent, e.recv), (4886, 31798))
        self.assertEqual(
            e.key,
            "/opt/brave.com/brave/brave --type=utility "
            "--utility-sub-type=network.mojom.NetworkService/4013/1000",
        )

    def test_connection_tuple_becomes_unknown(self):
        e = parse_trace_line("10.0.0.5:443-10.0.0.9:52133\t339\t503")
        self.assertEqual(e.app, "Unknown")
        self.assertIsNone(e.pid)
        self.assertIsNone(e.uid)
        self.assertEqual((e.sent, e.recv), (339, 503))

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

    def test_unrelated_counters_sharing_one_name_do_not_inflate(self):
        # Regression: the counters are keyed on nethogs' label, so two rows
        # that *display* as the same app must not be merged.  Merging them
        # made every switch look like a counter reset and re-added whole
        # values, inflating per-app totals by orders of magnitude.
        total = [0, 0]
        for key, sent, recv in [
            ("unknown TCP/0/0", 0, 0),
            ("10.0.0.5:443-10.0.0.9:52133", 339, 503),
            ("unknown TCP/0/0", 0, 0),
            ("10.0.0.5:443-10.0.0.9:52133", 339, 503),
            ("unknown TCP/0/0", 0, 0),
            ("10.0.0.5:443-10.0.0.9:52133", 678, 1006),
        ]:
            d_tx, d_rx = self.acc.feed(key, sent, recv)
            total[0] += d_tx
            total[1] += d_rx
        # 339+339+1006 would be the buggy answer for a *duplicated* 339+503
        self.assertEqual(total, [678, 1006])

    def test_same_name_different_processes_do_not_inflate(self):
        # Every Brave child is called "brave"; each has its own counter.
        total = [0, 0]
        for key, sent, recv in [
            ("brave/10/1000", 1000, 500),
            ("brave/20/1000", 2000, 900),
            ("brave/10/1000", 1500, 700),
            ("brave/20/1000", 2500, 1000),
        ]:
            d_tx, d_rx = self.acc.feed(key, sent, recv)
            total[0] += d_tx
            total[1] += d_rx
        # 1000 + 2000 + 500 + 500.  Merging the two counters would have
        # counted 1000 + 2000 + 1500 + 1000 = 5500 instead.
        self.assertEqual(total, [4000, 1700])


class ParseAndAccumulateTests(unittest.TestCase):
    """End-to-end: real trace lines -> parse -> accumulate.

    The accumulator tests above feed keys in by hand, and that is exactly how
    the pid got lost without anyone noticing: the parser stripped ``/pid/uid``
    off the key, so counters that were supposed to stay apart were merged -
    the very bug the hand-written keys were meant to prevent.
    """

    def test_same_command_line_different_pids_stay_apart(self):
        acc = TraceAccumulator()
        lines = [
            "python3 /home/u/worker.py/100/1000\t1000\t0",
            "python3 /home/u/worker.py/200/1000\t2000\t0",
            "python3 /home/u/worker.py/100/1000\t1500\t0",
            "python3 /home/u/worker.py/200/1000\t2600\t0",
        ]
        total = 0
        for line in lines:
            entry = parse_trace_line(line)
            self.assertIsNotNone(entry)
            d_tx, _d_rx = acc.feed(entry.key, entry.sent, entry.recv, entry.pid)
            total += d_tx
        # 1000 + 2000 + 500 + 600.  Merging the two counters (what a key
        # without the pid does) would have reported 1000+2000+1500+2600 = 7100.
        self.assertEqual(total, 4100)

    def test_unknown_tcp_and_connection_tuples_stay_apart(self):
        acc = TraceAccumulator()
        total = 0
        for line in [
            "unknown TCP/0/0\t0\t0",
            "10.0.0.5:443-10.0.0.9:52133\t339\t503",
            "unknown TCP/0/0\t0\t0",
            "10.0.0.5:443-10.0.0.9:52133\t339\t503",
        ]:
            entry = parse_trace_line(line)
            self.assertIsNotNone(entry)
            d_tx, _d_rx = acc.feed(entry.key, entry.sent, entry.recv, entry.pid)
            total += d_tx
        self.assertEqual(total, 339)          # not 339 counted twice

    def test_evict_dead_reclaims_finished_processes_only(self):
        acc = TraceAccumulator()
        acc.feed("brave/100/1000", 100, 100, pid=100)
        acc.feed("brave/200/1000", 50, 50, pid=200)
        acc.feed("unknown TCP/0/0", 1, 1, pid=0)
        acc.feed("10.0.0.5:443-10.0.0.9:52133", 5, 5, pid=None)

        alive = {200}.__contains__
        self.assertEqual(acc.evict_dead(alive), 1)
        self.assertEqual(acc.evict_dead(alive), 0)
        self.assertNotIn("brave/100/1000", acc._last)
        # a label that is still alive keeps its baseline, so its counter is
        # never counted a second time
        self.assertEqual(acc.feed("brave/200/1000", 80, 90, pid=200), (30, 40))
        # labels with no pid cannot be proven dead, so they are kept on purpose
        self.assertIn("unknown TCP/0/0", acc._last)
        self.assertIn("10.0.0.5:443-10.0.0.9:52133", acc._last)


if __name__ == "__main__":
    unittest.main()
