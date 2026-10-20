"""Helpers for turning ``nethogs`` trace output into per-application deltas.

nethogs is run in trace mode with byte units (``-t -v 2``).  In that mode each
refresh block lists lines shaped like::

    <program>/<pid>/<uid>\t<sent_bytes>\t<received_bytes>

where ``sent`` is upload and ``received`` is download, cumulative since
nethogs started.  We convert those cumulative figures into per-interval deltas
so they can be summed into daily totals.

The parser is deliberately tolerant: it accepts tab or multi-space separators
and never raises on a malformed line.
"""
from __future__ import annotations

import re
import time
from typing import Dict, Iterator, NamedTuple, Optional, Tuple

UNKNOWN_NAMES = {"unknown", "unknown tcp", "unknown udp", "unknown ip"}

#: How long a label may go unprinted before its baseline is forgotten.  nethogs
#: prints every row it knows about on every refresh, so a label that has been
#: silent for minutes is genuinely gone.  Ten minutes is a wide margin over the
#: 5 s refresh, and the only defence needed against a slow feed.
DEFAULT_STALE_AFTER = 600.0

# nethogs sometimes labels traffic it cannot tie to a process with the raw
# connection tuple (e.g. "10.0.0.5:443-10.0.0.9:52133").  Those are still
# "unknown" traffic - keep them out of the per-application list.
_CONN_TUPLE_RE = re.compile(
    r"^[0-9A-Fa-f:.]{3,}:\d{1,5}-[0-9A-Fa-f:.]{3,}:\d{1,5}$"
)


class TraceEntry(NamedTuple):
    """One nethogs trace row.

    ``key`` is nethogs' label for the row *exactly as printed*, including the
    trailing ``/pid/uid`` (or, for unattributable traffic, the raw connection
    tuple).  nethogs' cumulative counters belong to that label - two processes
    that merely share a command line, or two ``brave`` children, are distinct
    counters because their labels differ, so the label - never the display name
    and never the label with the pid stripped off - is what keys the deltas.
    """

    app: str
    pid: Optional[int]
    uid: Optional[int]
    sent: int
    recv: int
    key: str


def normalize_app(name: str) -> str:
    """Collapse an nethogs program label into a stable display name."""
    name = (name or "").strip()
    if not name:
        return "Unknown"
    # nethogs prints the *whole command line* for some processes (e.g. a
    # browser's network-service child), which would otherwise end up as a
    # 600-character blob in the panel tooltip.  Keep only the executable.
    name = name.split(None, 1)[0]
    # ...and it may print a full path; keep the basename.
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    if name.lower() in UNKNOWN_NAMES or name.lower().startswith("unknown"):
        return "Unknown"
    if _CONN_TUPLE_RE.match(name):
        return "Unknown"
    return name


def parse_trace_line(line: str) -> Optional[TraceEntry]:
    """Parse one trace line → :class:`TraceEntry`, or ``None`` if it is not a row."""
    line = line.rstrip("\r\n")
    if not line or line.startswith("Refreshing") or line.startswith("adding local"):
        return None

    parts = line.split("\t")
    if len(parts) < 3:
        parts = line.split()
    if len(parts) < 3:
        return None

    ident, sent_s, recv_s = parts[0], parts[1], parts[2]
    raw = ident                      # the counter's identity, pid/uid included
    pid = uid = None
    segs = ident.rsplit("/", 2)
    if len(segs) == 3 and segs[1].isdigit() and segs[2].isdigit():
        ident = segs[0]
        pid = int(segs[1])
        uid = int(segs[2])

    try:
        sent = int(float(sent_s))
        recv = int(float(recv_s))
    except ValueError:
        return None

    return TraceEntry(
        app=normalize_app(ident), pid=pid, uid=uid, sent=sent, recv=recv, key=raw
    )


class TraceAccumulator:
    """Convert nethogs' cumulative per-process figures into per-interval deltas.

    Counters are tracked per *nethogs label* (``TraceEntry.key``) - the label as
    printed, pid and uid included.  nethogs reports one cumulative counter per
    process, and several unrelated processes routinely share a display name
    (every Brave child is called ``brave``, and every row it cannot attribute is
    ``Unknown``).  Merging those counters makes each switch between them look
    like a counter reset, so the full value gets re-added over and over - which
    is what once inflated the per-application totals by orders of magnitude.

    A baseline is forgotten only once its row stops being printed
    (:meth:`evict_stale`).  Expiry is measured from the last time the row was
    *seen*, never from whether its process still exists: in trace mode nethogs
    0.8.7 only prunes exited processes in its rate view modes - ``-v 2`` (total
    bytes), which is what this monitor uses, keeps printing them forever.  The
    row's counter is frozen at its final value, so dropping its baseline makes
    the next refresh look like a brand new process and re-counts that whole
    counter, every flush, for as long as nethogs remembers it.
    """

    def __init__(self, stale_after: float = DEFAULT_STALE_AFTER) -> None:
        self.stale_after = float(stale_after)
        self._last: Dict[str, Tuple[int, int]] = {}
        self._seen: Dict[str, float] = {}
        self._last_feed = 0.0

    def feed(
        self,
        key: str,
        sent: int,
        recv: int,
        now: Optional[float] = None,
    ) -> Tuple[int, int]:
        """Return ``(d_tx, d_rx)`` for ``key`` since the previous feed.

        ``key`` is the nethogs label (``TraceEntry.key``), not the app name.

        Detects decreases (nethogs restart, counter reset) and treats the new
        value as the delta so nothing is lost or double-counted.
        """
        prev = self._last.get(key)
        if prev is None or sent < prev[0] or recv < prev[1]:
            d_sent, d_recv = sent, recv
        else:
            d_sent, d_recv = sent - prev[0], recv - prev[1]
        self._last[key] = (sent, recv)
        self._last_feed = time.monotonic() if now is None else now
        self._seen[key] = self._last_feed
        return d_sent, d_recv

    def evict_stale(
        self, max_age: Optional[float] = None, now: Optional[float] = None
    ) -> int:
        """Forget labels nethogs has stopped printing; return how many.

        A label is only ever dropped after it has been silent for ``max_age``
        seconds (default :data:`DEFAULT_STALE_AFTER`), because that is the only
        thing that proves nethogs is done with it.  Labels are large (nethogs
        prints the whole command line), so dead rows do get reclaimed - just
        after the row itself is gone.

        If the feed as a whole has stalled (suspend, hung nethogs) silence says
        nothing about any individual row, so nothing is dropped: the counters
        are still cumulative and the stored baselines are still valid.
        """
        now = time.monotonic() if now is None else now
        max_age = self.stale_after if max_age is None else max_age
        if not self._seen or now - self._last_feed > max_age:
            return 0
        stale = [key for key, seen in self._seen.items() if now - seen > max_age]
        for key in stale:
            self._last.pop(key, None)
            self._seen.pop(key, None)
        return len(stale)

    def label_count(self) -> int:
        """How many labels are remembered (one per row nethogs is printing)."""
        return len(self._last)

    def reset(self) -> None:
        self._last.clear()
        self._seen.clear()
        self._last_feed = 0.0


def iter_trace_lines(stream) -> Iterator[str]:
    """Yield lines from a text stream, tolerating partial/broken output."""
    for line in stream:
        yield line
