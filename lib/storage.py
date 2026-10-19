"""Tiny SQLite store for daily totals and per-application usage.

Design goals:
  * one small file (tens of KB), WAL mode, minimal writes;
  * exact daily totals that never double-count, even when sampled by more than
    one process (the panel and the collector both sample);
  * exactly 30 days of history, pruned automatically;
  * no history depends on the collector staying alive.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KEEP_DAYS = 30

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


class Storage:
    """All persistence lives here; everything else is pure logic."""

    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        # keep background work cheap; never block the panel on a lock
        conn.execute("PRAGMA busy_timeout=15000;")
        return conn

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
        self, now_rx: int, now_tx: int, when: Optional[date] = None
    ) -> Tuple[int, int]:
        """Add the delta since the previous sample to today's totals.

        Safe to call from several processes at once: the baseline counters are
        updated inside a single write transaction, so no byte is counted twice.
        Returns ``(rx_added, tx_added)``.
        """
        day = _day(when)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            last_rx = self._get_meta(conn, "last_rx")
            last_tx = self._get_meta(conn, "last_tx")

            if last_rx is None or last_tx is None:
                # First ever sample: establish the baseline, count nothing.
                d_rx = d_tx = 0
            else:
                last_rx = int(last_rx)
                last_tx = int(last_tx)
                d_rx = now_rx - last_rx
                d_tx = now_tx - last_tx
                # Counter reset (reboot or interface re-created): start fresh.
                if d_rx < 0:
                    d_rx = now_rx
                if d_tx < 0:
                    d_tx = now_tx

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
            self._set_meta(conn, "last_rx", now_rx)
            self._set_meta(conn, "last_tx", now_tx)
            self._set_meta(conn, "last_sample", stamp)
            conn.execute("COMMIT")
            return d_rx, d_tx
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_today(self, when: Optional[date] = None) -> Dict[str, Any]:
        day = _day(when)
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM daily WHERE date=?", (day,)).fetchone()
        finally:
            conn.close()
        if not row:
            return {"date": day, "rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0}
        return {
            "date": row["date"],
            "rx_bytes": row["rx_bytes"],
            "tx_bytes": row["tx_bytes"],
            "total_bytes": row["rx_bytes"] + row["tx_bytes"],
        }

    # ------------------------------------------------------------- per-app ---
    def add_app_delta(
        self, app: str, d_rx: int, d_tx: int, when: Optional[date] = None
    ) -> None:
        """Add a per-application delta to today's per-app total."""
        if d_rx == 0 and d_tx == 0:
            return
        day = _day(when)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO app_daily(date, app, rx_bytes, tx_bytes, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(date, app) DO UPDATE SET
                    rx_bytes   = rx_bytes   + excluded.rx_bytes,
                    tx_bytes   = tx_bytes   + excluded.tx_bytes,
                    updated_at = excluded.updated_at
                """,
                (day, app, d_rx, d_tx, stamp),
            )
            conn.commit()
        finally:
            conn.close()

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
        conn = self._connect()
        try:
            rows = conn.execute(sql, (day,)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ history ---
    def get_history(self, days: int = KEEP_DAYS) -> List[Dict[str, Any]]:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT date, rx_bytes, tx_bytes, (rx_bytes + tx_bytes) AS total_bytes "
                "FROM daily WHERE date >= ? ORDER BY date ASC",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
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

        conn = self._connect()
        try:
            found = {r["date"]: r for r in conn.execute(sql, params)}
        finally:
            conn.close()

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
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT app FROM app_daily WHERE date >= ? ORDER BY app",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
        return [r["app"] for r in rows]

    # ------------------------------------------------------------ pruning ---
    def prune(self, keep_days: int = KEEP_DAYS) -> int:
        """Delete records older than ``keep_days`` (keeps today + previous N-1)."""
        cutoff = (date.today() - timedelta(days=keep_days - 1)).isoformat()
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM daily WHERE date < ?", (cutoff,))
            removed = cur.rowcount
            conn.execute("DELETE FROM app_daily WHERE date < ?", (cutoff,))
            conn.commit()
            return removed
        finally:
            conn.close()
