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
from typing import Callable, Dict, Iterator, NamedTuple, Optional, Tuple

UNKNOWN_NAMES = {"unknown", "unknown tcp", "unknown udp", "unknown ip"}

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

    Keys are only forgotten when their process is provably gone
    (:meth:`evict_dead`): a label without a pid, or one whose pid is still
    alive, must keep its baseline or its whole counter would be counted again.
    """

    def __init__(self) -> None:
        self._last: Dict[str, Tuple[int, int]] = {}
        self._pids: Dict[str, int] = {}

    def feed(
        self, key: str, sent: int, recv: int, pid: Optional[int] = None
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
        if pid:
            self._pids[key] = pid
        return d_sent, d_recv

    def evict_dead(self, alive: Callable[[int], bool]) -> int:
        """Forget labels whose process no longer exists; return how many.

        nethogs keeps listing a process for as long as it lives, so a label
        carrying a pid that is gone can never be updated again - and those
        labels are large (nethogs prints the whole command line).  Labels
        without a pid (raw connection tuples) are deliberately kept: they
        cannot be proven dead, and dropping a live one would re-count it.
        """
        dead = [key for key, pid in self._pids.items() if not alive(pid)]
        for key in dead:
            self._last.pop(key, None)
            self._pids.pop(key, None)
        return len(dead)

    def reset(self) -> None:
        self._last.clear()
        self._pids.clear()


def iter_trace_lines(stream) -> Iterator[str]:
    """Yield lines from a text stream, tolerating partial/broken output."""
    for line in stream:
        yield line
