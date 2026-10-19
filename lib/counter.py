"""Exact network byte totals read from the Linux kernel (/proc/net/dev).

This is the trustworthy "total" engine. It needs no privileges and never
touches the network itself.  It is the same reliable source the previous
project used (Linux Data Guardian / byte_counter.py), trimmed down.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

PROC_NET_DEV = Path("/proc/net/dev")


def read_interfaces(path: Path = PROC_NET_DEV) -> Dict[str, Tuple[int, int]]:
    """Return ``{interface: (rx_bytes, tx_bytes)}`` for non-loopback interfaces.

    Loopback (``lo``) is excluded so local traffic is never counted as internet
    usage.  Unreadable or malformed entries are skipped rather than raising.
    """
    result: Dict[str, Tuple[int, int]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    for line in text.splitlines()[2:]:  # first two lines are headers
        if ":" not in line:
            continue
        iface, data = line.split(":", 1)
        iface = iface.strip()
        if not iface or iface == "lo":
            continue
        toks = data.split()
        if len(toks) >= 9:
            try:
                result[iface] = (int(toks[0]), int(toks[8]))
            except ValueError:
                continue
    return result


def read_totals(path: Path = PROC_NET_DEV) -> Tuple[int, int]:
    """Return cumulative ``(rx_bytes, tx_bytes)`` summed over counted interfaces."""
    rx_total = 0
    tx_total = 0
    for rx, tx in read_interfaces(path).values():
        rx_total += rx
        tx_total += tx
    return rx_total, tx_total


def format_bytes(n: int) -> str:
    """Human-readable size, e.g. ``1.2 GB``."""
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"
