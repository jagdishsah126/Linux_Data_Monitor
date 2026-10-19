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
from typing import Dict, Iterator, NamedTuple, Optional, Tuple

UNKNOWN_NAMES = {"unknown", "unknown tcp", "unknown udp", "unknown ip"}

# nethogs sometimes labels traffic it cannot tie to a process with the raw
# connection tuple (e.g. "10.0.0.5:443-10.0.0.9:52133").  Those are still
# "unknown" traffic - keep them out of the per-application list.
_CONN_TUPLE_RE = re.compile(
    r"^[0-9A-Fa-f:.]{3,}:\d{1,5}-[0-9A-Fa-f:.]{3,}:\d{1,5}$"
)


class TraceEntry(NamedTuple):
    """One nethogs trace row.

    ``key`` is nethogs' own label for the row (before normalisation): the
    program path plus pid/uid, or a raw connection tuple.  It stays the same
    for as long as that process/connection exists and is what the cumulative
    byte counters belong to, so it - not the display name - keys the deltas.
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
        app=normalize_app(ident), pid=pid, uid=uid, sent=sent, recv=recv, key=ident
    )


class TraceAccumulator:
    """Convert nethogs' cumulative per-process figures into per-interval deltas.

    Counters are tracked per *nethogs label* (``TraceEntry.key``), never per
    display name.  nethogs reports one cumulative counter per process, and
    several unrelated processes routinely share a display name: every Brave
    child is called ``brave``, and every row it cannot attribute is
    ``Unknown`` (``unknown TCP/0/0`` and the raw connection tuples).  Merging
    those counters makes each switch between them look like a counter reset,
    so the full value gets re-added over and over - which is what once
    inflated the per-application totals by orders of magnitude.
    """

    def __init__(self) -> None:
        self._last: Dict[str, Tuple[int, int]] = {}

    def feed(self, key: str, sent: int, recv: int) -> Tuple[int, int]:
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
        return d_sent, d_recv

    def reset(self) -> None:
        self._last.clear()


def iter_trace_lines(stream) -> Iterator[str]:
    """Yield lines from a text stream, tolerating partial/broken output."""
    for line in stream:
        yield line
