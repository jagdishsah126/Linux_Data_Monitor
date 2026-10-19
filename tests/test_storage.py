import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from unittest import mock

from storage import Storage  # noqa: E402


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "monitor.db"
        self.st = Storage(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_sample_establishes_baseline_only(self):
        added = self.st.sample_totals({"wlan0": (1_000_000, 500_000)})
        self.assertEqual(added, (0, 0))
        self.assertEqual(self.st.get_today()["total_bytes"], 0)

    def test_second_sample_adds_delta(self):
        self.st.sample_totals({"wlan0": (1000, 500)})
        added = self.st.sample_totals({"wlan0": (1500, 700)})   # +500 down, +200 up
        self.assertEqual(added, (500, 200))
        t = self.st.get_today()
        self.assertEqual(t["rx_bytes"], 500)
        self.assertEqual(t["tx_bytes"], 200)
        self.assertEqual(t["total_bytes"], 700)

    def test_multiple_samples_accumulate(self):
        self.st.sample_totals({"wlan0": (0, 0)})
        for i in range(1, 5):
            self.st.sample_totals({"wlan0": (i * 100, i * 10)})
        t = self.st.get_today()
        self.assertEqual(t["rx_bytes"], 400)
        self.assertEqual(t["tx_bytes"], 40)

    def test_counter_reset_is_handled(self):
        self.st.sample_totals({"wlan0": (1000, 500)})
        added = self.st.sample_totals({"wlan0": (10, 5)})   # driver reloaded: counter dropped
        self.assertEqual(added, (10, 5))

    def test_no_double_count_across_samples(self):
        # Two samplers using the same database must not double count.
        st2 = Storage(self.db)
        self.st.sample_totals({"wlan0": (0, 0)})
        self.st.sample_totals({"wlan0": (1000, 1000)})
        st2.sample_totals({"wlan0": (2000, 2000)})   # different process, same DB
        self.assertEqual(self.st.get_today()["total_bytes"], 4000)

    def test_app_totals(self):
        self.st.add_app_delta("brave", 500, 100)
        self.st.add_app_delta("brave", 250, 50)
        self.st.add_app_delta("apt", 10, 5)
        apps = {a["app"]: a for a in self.st.get_today_apps()}
        self.assertEqual(apps["brave"]["rx_bytes"], 750)
        self.assertEqual(apps["brave"]["total_bytes"], 900)
        self.assertEqual(apps["apt"]["tx_bytes"], 5)
        # sorted by total desc
        self.assertEqual(self.st.get_today_apps()[0]["app"], "brave")

    def test_zero_delta_is_ignored(self):
        self.st.add_app_delta("x", 0, 0)
        self.assertEqual(self.st.get_today_apps(), [])

    def test_bulk_app_deltas_match_single_calls(self):
        # The collector flushes a whole window in one transaction; the result
        # must be identical to adding each delta on its own.
        self.st.add_app_deltas({"brave": [500, 100], "apt": [10, 5]})
        self.st.add_app_deltas({"brave": [250, 50], "zero": [0, 0]})
        apps = {a["app"]: a for a in self.st.get_today_apps()}
        self.assertEqual(apps["brave"]["rx_bytes"], 750)
        self.assertEqual(apps["brave"]["tx_bytes"], 150)
        self.assertEqual(apps["apt"]["rx_bytes"], 10)
        self.assertNotIn("zero", apps)          # nothing to record
        self.assertEqual(self.st.add_app_deltas({}), None)

    def test_connection_is_reused(self):
        # One connection per thread, never closed per call - closing the last
        # connection makes SQLite checkpoint and rewrite the database.
        self.assertIs(self.st._connect(), self.st._connect())
        first = self.st._connect()
        self.st.add_app_delta("brave", 1, 1)
        self.assertIs(self.st._connect(), first)

    def test_prune_removes_old_rows(self):
        old = date.today() - timedelta(days=40)
        self.st.add_app_delta("oldapp", 10, 10, when=old)
        self.st.sample_totals({"wlan0": (0, 0)}, when=old)
        removed = self.st.prune(keep_days=30)
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(self.st.get_today_apps(when=old), [])

    def test_history_window(self):
        for d in (1, 5, 10):
            self.st.sample_totals({"wlan0": (0, 0)}, when=date.today() - timedelta(days=d))
        rows = self.st.get_history(days=30)
        self.assertEqual(len(rows), 3)

    def test_series_zero_fills_missing_days(self):
        day = date.today() - timedelta(days=3)
        self.st.sample_totals({"wlan0": (0, 0)}, when=day)          # establish baseline
        self.st.sample_totals({"wlan0": (1000, 400)}, when=day)     # +1000 down, +400 up

        series = self.st.get_series(days=7)
        self.assertEqual(len(series), 7)                 # one point per day
        self.assertEqual(series[-1]["date"], date.today().isoformat())
        self.assertEqual(series[0]["total_bytes"], 0)    # quiet day is still a point
        entry = next(s for s in series if s["date"] == day.isoformat())
        self.assertEqual((entry["rx_bytes"], entry["tx_bytes"]), (1000, 400))
        self.assertEqual(entry["total_bytes"], 1400)

    def test_series_can_track_one_app(self):
        self.st.add_app_delta("brave", 500, 100)
        self.st.add_app_delta("apt", 10, 5)
        self.st.add_app_delta("brave", 100, 50, when=date.today() - timedelta(days=2))

        series = self.st.get_series(days=7, app="brave")
        self.assertEqual(len(series), 7)
        self.assertEqual(series[-1]["total_bytes"], 600)
        self.assertEqual(series[-3]["total_bytes"], 150)   # 100 + 50, two days ago
        # a quiet day for that app is zero, not missing
        self.assertEqual(series[0]["total_bytes"], 0)

    def test_interface_disappearing_never_inflates(self):
        # The old code kept ONE baseline for the sum of all interfaces.  When
        # tunnel/virtual interface B went away, the sum fell by B's whole
        # lifetime counter - indistinguishable from a reset - and the entire
        # current total was credited to today.
        self.st.sample_totals({"wlan0": (1000, 100), "tun0": (2000, 200)})
        added = self.st.sample_totals({"wlan0": (1100, 150)})     # tun0 gone
        self.assertEqual(added, (100, 50))                        # wlan0's delta only
        self.assertEqual(self.st.get_today()["total_bytes"], 150)

    def test_new_interface_counts_from_its_own_zero(self):
        self.st.sample_totals({"wlan0": (1000, 100)})
        added = self.st.sample_totals({"wlan0": (1000, 100), "docker0": (50, 5)})
        self.assertEqual(added, (50, 5))        # a fresh interface starts near zero

    def test_reboot_rebaselines_without_guessing(self):
        with mock.patch("storage.read_boot_id", return_value="boot-1"):
            self.st.sample_totals({"wlan0": (5000, 1000)})
            self.st.sample_totals({"wlan0": (6000, 1200)})
            self.assertEqual(self.st.get_today()["total_bytes"], 1200)
            # A reboot between samples: the counters restarted, and the traffic
            # from the gap cannot be attributed to any day, so nothing is added.
            with mock.patch("storage.read_boot_id", return_value="boot-2"):
                added = self.st.sample_totals({"wlan0": (700, 100)})
        self.assertEqual(added, (0, 0))
        self.assertEqual(self.st.get_today()["total_bytes"], 1200)

    def test_unparseable_baseline_is_adopted_not_guessed(self):
        conn = self.st._connect()
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('iface_baseline', '{{{not json') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        added = self.st.sample_totals({"wlan0": (9_000_000, 5)})
        self.assertEqual(added, (0, 0))

    def test_app_names_are_recent_and_sorted(self):
        self.st.add_app_delta("brave", 1, 1)
        self.st.add_app_delta("apt", 1, 1)
        self.st.add_app_delta("ancient", 1, 1, when=date.today() - timedelta(days=60))
        self.assertEqual(self.st.get_app_names(days=30), ["apt", "brave"])


if __name__ == "__main__":
    unittest.main()
