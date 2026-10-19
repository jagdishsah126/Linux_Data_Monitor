"""Tiny SQLite store for daily totals and per-application usage.

Design goals:
  * one small file (tens of KB), WAL mode, minimal writes;
  * exact daily totals that never double-count, even when sampled by more than
    one process (the panel and the collector both sample);
  * exactly 30 days of history, pruned automatically;
  * no history depends on the collector staying alive.

The totals baseline is kept **per interface** (see :meth:`Storage.sample_totals`).
A single baseline for the sum of all interfaces looks equivalent, but it is not:
whenever an interface appears or disappears the sum moves by that interface's
whole lifetime counter, which is indistinguishable from a counter reset.

Write volume matters here: SQLite checkpoints the WAL into the database and
deletes the WAL file when the *last* connection closes, so opening a fresh
connection for every little update rewrote the database several times a second.
Connections are therefore cached per thread and kept open; call :meth:`close`
to release one.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

KEEP_DAYS = 30

BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

DEFAULT_DB = Path(
    os.environ.get("DATA_MONITOR_DB")
    or (Path.home() / ".local" / "share" / "data-monitor" / "monitor.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS daily (
    date       TEXT PRIMARY KEY,
    rx_bytes   INTEGER NOT NULL DEFAULT 0,
    tx_bytes   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS app_daily (
    date       TEXT NOT NULL,
    app        TEXT NOT NULL,
    rx_bytes   INTEGER NOT NULL DEFAULT 0,
    tx_bytes   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (date, app)
);
CREATE INDEX IF NOT EXISTS idx_app_daily_date ON app_daily(date);
"""


def _day(when: Optional[date] = None) -> str:
    return (when or date.today()).isoformat()


def read_boot_id(path: Path = BOOT_ID_PATH) -> Optional[str]:
    """The kernel's per-boot UUID (None when it cannot be read).

    Used to recognise a reboot between two samples, which is the one case where
    every counter legitimately jumps backwards.
    """
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class Storage:
    """All persistence lives here; everything else is pure logic."""

    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        conn = self._connect()          # cached, deliberately not closed
        conn.executescript(SCHEMA)
        # `last_rx`/`last_tx` were the old whole-system baseline; per-interface
        # baselines replaced them and the stale rows would only confuse a reader.
        conn.execute("DELETE FROM meta WHERE key IN ('last_rx', 'last_tx')")
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Return this thread's connection, opening it once and keeping it."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            # keep background work cheap; never block the panel on a lock
            conn.execute("PRAGMA busy_timeout=15000;")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close this thread's cached connection (the process exit does too)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ---------------------------------------------------------------- meta ---
    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # -------------------------------------------------------------- totals ---
    def sample_totals(
        self, interfaces: Mapping[str, Tuple[int, int]], when: Optional[date] = None
    ) -> Tuple[int, int]:
        """Add the delta since the previous sample to today's totals.

        ``interfaces`` is ``{name: (rx_bytes, tx_bytes)}`` as read from
        ``/proc/net/dev``.  Safe to call from several processes at once: the
        baselines are updated inside a single write transaction, so no byte is
        counted twice.  Returns ``(rx_added, tx_added)``.

        Baselines are kept **per interface**, because comparing one baseline
        against the *sum* of the interfaces breaks whenever the interface set
        changes.  If interface B disappears, the sum drops by B's whole
        lifetime counter - which looks exactly like a counter reset, and the
        old code then credited the entire current total to today.  Rules:

        * new interface, or a counter that went backwards (driver reload):
          its whole current value is added;
        * an interface that disappears simply stops contributing; the bytes in
          the last partial interval on it are lost, never double-counted;
        * after a reboot (``boot_id`` changed) or on the very first sample
          every interface is re-baselined and nothing is added, because traffic
          from before the monitor was running cannot be attributed to a day.
        """
        day = _day(when)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            boot = read_boot_id()
            prev_boot = self._get_meta(conn, "boot_id")
            baseline = self._load_baseline(conn)

            rebooted = (
                boot is not None and prev_boot is not None and boot != prev_boot
            )
            d_rx = d_tx = 0
            for iface, (rx, tx) in interfaces.items():
                prev = baseline.get(iface)
                if prev is None or rx < prev[0] or tx < prev[1] or rebooted:
                    add_rx, add_tx = rx, tx
                else:
                    add_rx, add_tx = rx - prev[0], tx - prev[1]
                d_rx += add_rx
                d_tx += add_tx

            if not baseline or rebooted:
                # First ever sample (or a fresh boot): adopt the counters as the
                # baseline and add nothing - never guess who the bytes belonged to.
                d_rx = d_tx = 0

            conn.execute(
                """
                INSERT INTO daily(date, rx_bytes, tx_bytes, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    rx_bytes   = rx_bytes   + excluded.rx_bytes,
                    tx_bytes   = tx_bytes   + excluded.tx_bytes,
                    updated_at = excluded.updated_at
                """,
                (day, d_rx, d_tx, stamp),
            )
            self._set_meta(
                conn,
                "iface_baseline",
                json.dumps({name: [val[0], val[1]] for name, val in interfaces.items()}),
            )
            if boot is not None:
                self._set_meta(conn, "boot_id", boot)
            self._set_meta(conn, "last_sample", stamp)
            conn.execute("COMMIT")
            return d_rx, d_tx
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    @staticmethod
    def _load_baseline(conn: sqlite3.Connection) -> Dict[str, List[int]]:
        """Read the per-interface baseline, tolerating anything unparseable."""
        raw = Storage._get_meta(conn, "iface_baseline")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        clean: Dict[str, List[int]] = {}
        for name, value in parsed.items():
            if isinstance(name, str) and isinstance(value, (list, tuple)) and len(value) == 2:
                try:
                    clean[name] = [int(value[0]), int(value[1])]
                except (TypeError, ValueError):
                    continue
        return clean

    def get_today(self, when: Optional[date] = None) -> Dict[str, Any]:
        day = _day(when)
        row = self._connect().execute(
            "SELECT * FROM daily WHERE date=?", (day,)
        ).fetchone()
        if not row:
            return {"date": day, "rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0}
        return {
            "date": row["date"],
            "rx_bytes": row["rx_bytes"],
            "tx_bytes": row["tx_bytes"],
            "total_bytes": row["rx_bytes"] + row["tx_bytes"],
        }

    # ------------------------------------------------------------- per-app ---
    def add_app_deltas(
        self, deltas: Mapping[str, Sequence[int]], when: Optional[date] = None
    ) -> None:
        """Add several per-application deltas in a **single** transaction.

        The collector batches its updates so a flush costs one commit instead
        of one per application per refresh - fewer WAL pages, fewer disk writes.
        """
        rows = [
            (app, int(pair[0]), int(pair[1]))
            for app, pair in deltas.items()
            if pair[0] or pair[1]
        ]
        if not rows:
            return
        day = _day(when)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                """
                INSERT INTO app_daily(date, app, rx_bytes, tx_bytes, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(date, app) DO UPDATE SET
                    rx_bytes   = rx_bytes   + excluded.rx_bytes,
                    tx_bytes   = tx_bytes   + excluded.tx_bytes,
                    updated_at = excluded.updated_at
                """,
                [(day, app, rx, tx, stamp) for app, rx, tx in rows],
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def add_app_delta(
        self, app: str, d_rx: int, d_tx: int, when: Optional[date] = None
    ) -> None:
        """Add one per-application delta to today's per-app total."""
        self.add_app_deltas({app: (d_rx, d_tx)}, when=when)

    def get_today_apps(
        self, when: Optional[date] = None, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        day = _day(when)
        sql = (
            "SELECT app, rx_bytes, tx_bytes, (rx_bytes + tx_bytes) AS total_bytes "
            "FROM app_daily WHERE date=? ORDER BY total_bytes DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self._connect().execute(sql, (day,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ history ---
    def get_history(self, days: int = KEEP_DAYS) -> List[Dict[str, Any]]:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = self._connect().execute(
            "SELECT date, rx_bytes, tx_bytes, (rx_bytes + tx_bytes) AS total_bytes "
            "FROM daily WHERE date >= ? ORDER BY date ASC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_series(
        self, days: int = KEEP_DAYS, app: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """One entry per day for the last ``days`` days, oldest first.

        ``app=None`` uses the exact daily totals; otherwise the per-application
        table.  Missing days are zero-filled so a chart always has ``days``
        evenly spaced points.
        """
        first = date.today() - timedelta(days=days - 1)
        cutoff = first.isoformat()
        if app is None:
            sql = "SELECT date, rx_bytes, tx_bytes FROM daily WHERE date >= ?"
            params: Tuple[Any, ...] = (cutoff,)
        else:
            sql = ("SELECT date, rx_bytes, tx_bytes FROM app_daily "
                   "WHERE date >= ? AND app = ?")
            params = (cutoff, app)

        found = {r["date"]: r for r in self._connect().execute(sql, params)}

        series: List[Dict[str, Any]] = []
        for offset in range(days):
            day = (first + timedelta(days=offset)).isoformat()
            row = found.get(day)
            rx = int(row["rx_bytes"]) if row else 0
            tx = int(row["tx_bytes"]) if row else 0
            series.append(
                {"date": day, "rx_bytes": rx, "tx_bytes": tx, "total_bytes": rx + tx}
            )
        return series

    def get_app_names(self, days: int = KEEP_DAYS) -> List[str]:
        """Application names with per-app data in the last ``days`` days."""
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = self._connect().execute(
            "SELECT DISTINCT app FROM app_daily WHERE date >= ? ORDER BY app",
            (cutoff,),
        ).fetchall()
        return [r["app"] for r in rows]

    # ------------------------------------------------------------ pruning ---
    def prune(self, keep_days: int = KEEP_DAYS) -> int:
        """Delete records older than ``keep_days`` (keeps today + previous N-1)."""
        cutoff = (date.today() - timedelta(days=keep_days - 1)).isoformat()
        conn = self._connect()
        cur = conn.execute("DELETE FROM daily WHERE date < ?", (cutoff,))
        removed = cur.rowcount
        conn.execute("DELETE FROM app_daily WHERE date < ?", (cutoff,))
        conn.commit()
        return removed
