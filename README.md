# Data Monitor

**Today's network data usage, right in your XFCE panel — click for a per-application breakdown and a 30-day chart.**

A tiny, *measurement-only* data tracker. It does not block, throttle, shape or enforce anything: it
just tells you the truth, cheaply.

```
 ┌──────────────────────────────────────────────┐
 │  ...  🔋  🔊  🕒                    ▲▼ 98.1 MB │   ← the panel item
 └──────────────────────────────────────────────┘
                                            │ click
                                            ▼
                          ┌─────────────────────────────────────┐
                          │ Today: 98.1 MB  (↓ 71.4 MB ↑ 26.7 MB)│
                          ├─────────────────────────────────────┤
                          │ opencode    down 4.6 MB  up 10.8 MB │
                          │ brave       down 2.1 MB  up 1.2 MB  │
                          │ Unknown     down 3.0 MB  up 0.4 MB  │
                          └─────────────────────────────────────┘
```

## Why this exists

Most "data saver" tools try to *control* your traffic — firewalls, quotas, download approval gates,
background root daemons. They are fragile, they need privileges, and when they fail they fail
silently. This project deliberately does the opposite and **only measures**:

| | |
| --- | --- |
| **Exact totals** | Read straight from the kernel (`/proc/net/dev`). No privilege needed. |
| **Per-application usage** | From [`nethogs`](https://github.com/raboof/nethogs) (~34 KB), given one narrow, reversible capability. |
| **No root daemon** | The collector runs as a **user** service. |
| **No firewall, no enforcement** | Nothing is ever blocked. Network configuration is untouched. |
| **No cloud, no telemetry** | Nothing leaves your machine. The tool uses **zero bytes** of network itself. |
| **Genuinely cheap** | ~8 MB RAM and one small SQLite file. |

## Features

- **Panel indicator** showing today's total (download + upload), refreshed every 30 s.
- **Hover tooltip** with today's split and the top applications.
- **Click** for a window with two tabs: today's per-application list, and a 30-day line graph you can
  filter to a single application.
- **30-day history** in a single SQLite file, pruned automatically.
- **Command line**: `data-monitor today`, `data-monitor apps`, `data-monitor history`.
- **Reversible**: a one-line uninstall and a `setcap -r` puts everything back.

## Requirements

- Linux with an **XFCE** panel (`xfce4-panel`) — the indicator uses `xfce4-genmon-plugin`.
- **Python 3.10+** with `tkinter` (popup) and `sqlite3` (storage) — both ship with most distros.
- `systemd --user` for the background collector.
- For per-application *names*: `nethogs` (see [Install](#install)).

Developed and tested on Linux Mint 22.3 "Zena" (Ubuntu 24.04) with XFCE 4.18 / `xfce4-panel 4.18.4`.

## Install

Two steps. The **total works after step 2 alone** — per-app names need step 1.

### Step 1 — system packages (needs `sudo`, once)

```bash
git clone https://github.com/jagdishsah126/Linux_Data_Monitor.git
cd Linux_Data_Monitor
sudo bash scripts/install-system.sh
```

This installs two small packages and grants `nethogs` one **narrow, reversible** capability:

| Package | Size (`.deb`) | Purpose |
| --- | --- | --- |
| `xfce4-genmon-plugin` | ≈ 52 KB | The panel indicator. |
| `nethogs` | ≈ 34 KB | Per-application attribution. |

```bash
# the only privileged change the project makes to your system:
sudo setcap cap_net_raw,cap_net_admin+eip /usr/sbin/nethogs
```

It does **not** install a root daemon, does **not** change firewall rules, and does **not** touch your
network configuration.

**Undo at any time:** `sudo setcap -r /usr/sbin/nethogs`

### Step 2 — user setup (no `sudo`)

```bash
bash scripts/install-user.sh
```

This links the commands into `~/.local/bin`, enables the **user** service
`data-monitor-collector.service`, and adds **one new item** to your existing XFCE panel. Your current
panel layout is not reset or reconfigured — nothing else on the panel is touched.

> If the panel item does not appear immediately, re-run `bash scripts/setup-panel.sh` or restart the
> panel once (`xfce4-panel -r`). See [The genmon gotcha](#the-genmon-gotcha).

## Usage

| Where | What |
| --- | --- |
| Panel | `▲▼ 98.1 MB` — today's download + upload. |
| Hover | Quick breakdown: today's split and the top applications. |
| Click | Today's per-application list, plus a 30-day chart tab. |
| Terminal | `data-monitor today` · `data-monitor apps` · `data-monitor history` |

```
$ data-monitor today
Today: 98.1 MB total (down 71.4 MB, up 26.7 MB)

$ data-monitor apps
opencode    down   4.6 MB    up  10.8 MB    total  15.4 MB
brave       down   2.1 MB    up   1.2 MB    total   3.3 MB
Unknown     down   3.0 MB    up   0.4 MB    total   3.4 MB
TOTAL (all)                  total  98.1 MB   [per-app is best-effort]
```

## How it works

```
┌─────────────────────────┐
│  /proc/net/dev          │  exact byte counters, free, no privilege
└───────────┬─────────────┘
            │ every 60 s
            ▼
┌─────────────────────────┐        ┌──────────────────────────┐
│  dm-collector           │◄───────│  nethogs -t -d 5 -v 2    │  per-application
│  (user systemd service) │        │  (one capability flag)   │  attribution
└───────────┬─────────────┘        └──────────────────────────┘
            ▼
┌─────────────────────────┐
│  SQLite  (~36 KB)       │  daily totals + per-app, 30-day prune
└───────┬─────────┬───────┘
        │         │
        ▼         ▼
   dm-panel    dm-popup
   (genmon)    (click → window)
```

- **Totals** are the difference between consecutive `/proc/net/dev` readings, per day. Reboots and
  interface changes are detected and handled.
- **Per-app** usage comes from `nethogs` trace mode; consecutive samples are summed as deltas.
- The panel asks for the current number every 30 s (`dm-panel` + `xfce4-genmon-plugin`).

## Accuracy — an honest summary

- ✅ **The total is exact.** It includes every application and all system traffic, and it survives
  reboots and network changes.
- ⚠️ **Per-app is best-effort.** Traffic that cannot be attributed to a process (other users, system
  services, very short-lived connections) is shown as a single **`Unknown`** row instead of being
  hidden. Per-app rows will therefore **not** sum exactly to the total.
- ⚠️ **Per-app figures only exist from the moment the capability is granted** — there is no history
  to back-fill.
- ⚠️ **The first day is partial.** Today's total starts counting when the tool is first installed, so
  day one may understate the hours before that. Every following day is complete.
- ⚠️ **VPNs:** if you use one, decide whether to count the VPN interface — counting both it and the
  physical interface would double-count.

## Uninstall

```bash
bash scripts/uninstall.sh            # removes everything, keeps your history
bash scripts/uninstall.sh --purge    # ...and deletes the recorded history too
sudo setcap -r /usr/sbin/nethogs     # drop the capability
```

Nothing outside the project folder, `~/.local`, `~/.config/xfce4` and the two packages is ever touched.

## Repository layout

```
Linux_Data_Monitor/
├── README.md                      ← install & usage (you are here)
├── Read.md                        ← full design notes, decisions and discoveries
├── bin/
│   ├── data-monitor               ← `today` / `apps` / `history` CLI
│   ├── dm-collector               ← reads totals + runs nethogs, writes to SQLite
│   ├── dm-panel                   ← prints the panel text (called by genmon)
│   └── dm-popup                   ← the click-to-open per-app window (tkinter)
├── lib/
│   ├── counter.py                 ← totals from /proc/net/dev
│   ├── storage.py                 ← SQLite schema, queries, 30-day prune
│   └── nethogs_parse.py           ← per-app tracer parsing
├── systemd/
│   └── data-monitor-collector.service   ← USER service (no root)
├── scripts/
│   ├── install-system.sh          ← sudo: packages + the one reversible setcap
│   ├── install-user.sh            ← no sudo: ~/.local/bin links + user service + panel item
│   ├── setup-panel.sh             ← add / repair (`--remove`) the panel item
│   └── uninstall.sh               ← cleanly reverses everything
└── tests/                         ← 33 unit tests, no network, no writes outside temp
```

Run the tests any time:

```bash
python3 -m unittest discover -s tests
```

## The genmon gotcha

`xfce4-genmon-plugin` **4.1.x** does **not** store its settings in `xfconf` the way other panel items
do. It reads a legacy, group-less `Key=Value` file instead:

```
~/.config/xfce4/panel/genmon-<plugin-id>.rc
```

```
Command=/home/<you>/.local/bin/dm-panel --markup
UseLabel=0
Text=
UpdatePeriod=30000
```

If that file is missing or wrong, the panel shows `(genmon)XXX` — genmon's "no command / no output"
placeholder — no matter what you write to `xfconf`. `scripts/setup-panel.sh` writes the file correctly,
so the item can be added or repaired at any time. More detail in [Read.md](Read.md) § 0.5.

## License

MIT — see [LICENSE](LICENSE).

## Contact

Questions, bug reports, suggestions, advice — or just some talk:
**[jagdishsah.com.np](https://jagdishsah.com.np)**

