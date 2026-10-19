"""Exact network byte totals read from the Linux kernel (/proc/net/dev).

This is the trustworthy "total" engine. It needs no privileges and never
touches the network itself.  It is the same reliable source the previous
project used (Linux Data Guardian / byte_counter.py), trimmed down.

What "total" means here, precisely: the sum of the counters of every *counted*
interface.  ``/proc/net/dev`` is a per-interface statistics interface, not a
deduplicated internet-usage counter, so a list of interfaces that contain
overlapping layers would count one packet several times.  Loopback is always
excluded, and so are the virtual devices that are never the machine's own
external link - their traffic is also visible on the physical interface that
carries it, so counting them too is pure double-counting.  Tunnels (VPN) are
*not* excluded by default, because which side of a tunnel you want to measure
is a real choice; ``DATA_MONITOR_EXCLUDE`` changes any of this without a code
change.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

PROC_NET_DEV = Path("/proc/net/dev")

#: Virtual interfaces that always sit *in front of* a counted physical device.
#: Container/virtualisation bridges are the common case (a container download
#: appears on the veth pair, on docker0 and on the Wi-Fi interface: three
#: counters, one download).
DEFAULT_EXCLUDE: Tuple[str, ...] = (
    "docker*",
    "veth*",
    "virbr*",
    "br-*",
    "vmnet*",
    "vboxnet*",
)

#: Set to a comma-separated glob list to replace :data:`DEFAULT_EXCLUDE`; set to
#: the empty string to count every interface except loopback.
EXCLUDE_ENV = "DATA_MONITOR_EXCLUDE"


def exclude_patterns(spec: Optional[str] = None) -> Tuple[str, ...]:
    """Resolve the exclusion globs.

    ``spec=None`` reads ``DATA_MONITOR_EXCLUDE``: unset means the default list,
    an empty string means "exclude nothing but loopback".
    """
    if spec is None:
        spec = os.environ.get(EXCLUDE_ENV)
        if spec is None:
            return DEFAULT_EXCLUDE
    spec = spec.strip()
    if not spec:
        return ()
    return tuple(part.strip() for part in spec.split(",") if part.strip())


def is_counted(iface: str, patterns: Sequence[str]) -> bool:
    """True if ``iface`` should contribute to the total."""
    if not iface or iface == "lo":
        return False
    return not any(fnmatch.fnmatchcase(iface, pattern) for pattern in patterns)


def read_interfaces(
    path: Path = PROC_NET_DEV, exclude: Optional[str] = None
) -> Dict[str, Tuple[int, int]]:
    """Return ``{interface: (rx_bytes, tx_bytes)}`` for counted interfaces.

    ``exclude`` overrides the pattern list (see :func:`exclude_patterns`).
    Unreadable or malformed entries are skipped rather than raising.
    """
    patterns = exclude_patterns(exclude)
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
        if not is_counted(iface, patterns):
            continue
        toks = data.split()
        if len(toks) >= 9:
            try:
                result[iface] = (int(toks[0]), int(toks[8]))
            except ValueError:
                continue
    return result


def read_totals(
    path: Path = PROC_NET_DEV, exclude: Optional[str] = None
) -> Tuple[int, int]:
    """Return cumulative ``(rx_bytes, tx_bytes)`` summed over counted interfaces."""
    rx_total = 0
    tx_total = 0
    for rx, tx in read_interfaces(path, exclude).values():
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
