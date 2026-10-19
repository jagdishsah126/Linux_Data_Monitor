# Data Monitor — Design & Implementation Notes

**Project:** a tiny XFCE panel applet that shows **today's total network data usage**, with a
click-to-open **per-application** breakdown.

This document is the original plan (kept for reference) plus notes on what was actually built and the
non-obvious things discovered along the way. See **`README.md`** for installation and usage.

**Reference platform:** Linux Mint 22.3 "Zena" (Ubuntu 24.04 noble) · XFCE 4.18 · `xfce4-panel 4.18.4`

**Key decisions:**
- ✅ Panel method → **`xfce4-genmon-plugin`** (~52 KB, one time)
- ✅ Scope → today's total **plus per-application usage**
- ✅ Per-app engine → **`nethogs`** (accurate, incl. UDP/QUIC video)
- ✅ One-time downloads → **~86 KB total** plus one reversible `setcap` command
- ✅ Build order → **build total + per-app together**
- ✅ Reuse the good parts of the earlier `Linux Data Guardian` prototype (copy only)

> ✅ **Status: implemented.** The panel shows today's total (e.g. `▲▼ 69.2 MB`) and clicking it opens the
> per-application list. The collector user-service is enabled and 47 unit tests pass.

---

## 0. Quick start (where to look first)

### 0.1 What gets installed

| Item | State after setup |
| --- | --- |
| `xfce4-genmon-plugin` (~52 KB) | installed |
| `nethogs` (~34 KB) | installed |
| Today's **total** in the panel | ✅ refreshes every 30 s |
| Click the panel number → per-app window + 30-day chart | ✅ |
| Collector user-service (writes history) | ✅ enabled, `~16 MB` RSS (`8 MB` PSS) + nethogs `~8 MB` |
| Disk writes | ✅ `~60 MB/day` (measured via `/proc/<pid>/io`; batching keeps it from being gigabytes) |
| Unit tests | ✅ 47 / 47 pass |
| Panel item | ✅ one new item; nothing else on the panel is touched |

The panel value is the **exact** kernel total (`/proc/net/dev`). The tool never uses the network itself.

### 0.2 The one `sudo` step ⚠️

Per-application *names* need one narrow, reversible permission on the `nethogs` program:

```bash
cd Linux_Data_Monitor          # wherever you cloned the project
sudo bash scripts/install-system.sh
```

Before it runs, `getcap /usr/sbin/nethogs` prints nothing; afterwards it prints
`cap_net_raw,cap_net_admin+eip`. It installs nothing new and changes nothing else.

**To undo it at any time:** `sudo setcap -r /usr/sbin/nethogs`.

Without it, the panel total is still exact — only the popup's per-app rows stay empty.

### 0.3 How to use it

- **Panel:** the number on the panel, e.g. `▲▼ 69.2 MB` — today's upload + download.
- **Hover** it for a quick breakdown (today + top applications).
- **Click** it for the full per-application window.
- **Command line:** `data-monitor today` and `data-monitor apps`.

### 0.4 How to remove it

```bash
bash scripts/uninstall.sh            # keep history
bash scripts/uninstall.sh --purge    # delete history too
sudo setcap -r /usr/sbin/nethogs
```

### 0.5 The important discovery (why the panel was tricky)

`xfce4-genmon-plugin` **4.1.x** does **not** store its settings in xfconf like other panel items. It
reads a legacy file:

```
~/.config/xfce4/panel/genmon-<plugin-id>.rc      # e.g. genmon-38.rc
```

with plain `Key=Value` lines and **no `[group]` header**:

```
Command=/home/<you>/.local/bin/dm-panel --markup
UseLabel=0
Text=
UpdatePeriod=30000
```

This is why the item first showed `(genmon)XXX` (genmon's "no command / no output" placeholder) no
matter what was written to xfconf. `scripts/setup-panel.sh` writes this file correctly, so the panel
item can be added or repaired at any time.

---

## 1. The 30-second version (plain English)

Two things are needed:

**(A) Today's total** — easy and exact. Your Linux kernel already counts every byte through your network card in a free file called `/proc/net/dev`. A tiny script reads it every 30 seconds, adds up the difference, and prints one number in the panel. **No root. No downloads for this part. It never uses the internet itself.**

**(B) Per-application usage** — this is the hard part, and I must be honest about why.

> Imagine one water pipe feeding the whole house. The meter at the wall knows the total, but it cannot tell you which tap used the water. Linux works the same way: all apps share **one** network "pipe", so the kernel's total cannot be split per app by itself.

To attribute usage to individual apps we must look at the actual traffic. The reliable tool for that is **`nethogs`** — it is **only 34 KB** and, importantly, most of its system dependencies are normally **already installed** (they come with `tcpdump`). It does need one narrow permission (packet-capture), which we grant **once** with a single `sudo` command — **we do not run anything as a permanent root daemon.**

**Total one-time downloads for the whole plan: roughly 86 KB** (52 KB plugin + 34 KB nethogs). Everything else is already present on a normal Linux desktop.

The catch to understand: per-app numbers are **best-effort**, not perfect (see Section 8). The *total* stays exact.

---

## 2. The original brief vs. the delivered plan

The original request: *"a data monitor in the panel showing today's total data usage, plus per-app; keep
RAM, disk and data low."*

The source brief proposed a much larger "guardian": firewall enforcement, download approval gates,
quotas, window tracking, a privileged helper, dual accounting, 30-day history, graphs.

**Do not build that as-is.**

### 2.1 Where the original brief is right

| Good point | Why |
| --- | --- |
| Measure first; minimise resources | Correct. |
| Use `/proc/net/dev` for interface totals | Exactly right and free. |
| Warn that per-app cannot be byte-perfect | Honest — and true (see Section 8). |
| Read-only, no payloads, no cloud | Correct for privacy. |
| Approval gates before system changes | Good safety culture. |

### 2.2 Where the original brief goes wrong

| Problem | Reality |
| --- | --- |
| Treats per-app as routine | It needs packet capture (privilege) — a real sub-project, not a checkbox. |
| Proposes a root helper | Root is what broke the old project. We use a **one-time capability flag** instead, with **no** root daemon. |
| Makes 30-day history + graphs core | Nice-to-have; add later. Bundling them is how projects balloon. *(Delivered later, and deliberately tiny: history is one table, the chart is ~60 lines of plain `tkinter` `Canvas` — no `matplotlib`.)* |
| Suggests evaluating nethogs/vnstat | vnstat gives only per-interface totals (no per-app). nethogs is the relevant one — and it is tiny. |
| Assumes a permanent daemon | Totals need none. Only per-app needs a small collector, and it can be a **user** service. |

### 2.3 Why the old project failed — and how this plan avoids every one of those failures

From the earlier project's own audit:

| Old failure | Cause | Avoided because… |
| --- | --- | --- |
| 🔴 Firewall dead | `sudo -n nft` needed a password; failed silently | **No firewall at all.** |
| 🔴 Notifications broken | Background systemd service had no desktop session | Panel runs in the user session; no system daemon. |
| 🔴 Root `Path.home()` bug | Ran as root → looked in `/root/…` | **Nothing runs as root.** |
| 🟡 Per-network table never updated | Daemon passed empty IDs | One source of truth; simple writes. |
| 🔴 Quota couldn't stop downloads | Live TCP connections ignore new-connection blocks | We block nothing. |
| 🔴 "Strict mode" = kill all networking | Untested foreground logic | Feature dropped. |

**Lesson:** the old project died of *enforcement*. This project is pure *measurement*, so all of those failure modes disappear.

---

## 3. Reference environment (read-only inspection)

The original build was developed on a low-spec laptop running Linux Mint; the numbers below are what the
tool was designed and measured against. What actually matters is modest: an XFCE panel, a kernel with
`/proc/net/dev`, and (optionally) `nethogs` for per-app names.

| Fact | Value |
| --- | --- |
| Distribution | Linux Mint 22.3 "Zena" (Ubuntu 24.04 / `noble`), XFCE 4.18, `xfce4-panel 4.18.4` |
| Kernel / arch | Linux `x86_64` with `/proc/net/dev` |
| RAM / disk | small (≈4 GB RAM) — the design deliberately avoids resident daemons |
| Python | 3.10+ (developed on 3.12); `tkinter` for the popup |
| Counter source | `/proc/net/dev` present ✔ |
| **`xfce4-genmon-plugin`** | apt candidate `.deb` **≈ 52 KB** (Ubuntu/Mint `universe`) |
| **`nethogs`** | apt candidate `.deb` **≈ 34 KB**; deps `libpcap0.8t64`, `libncurses6`, `libtinfo6`, `libstdc++6` are normally already present |
| Packet-capture libs | `libpcap` (usually already present via `tcpdump`) |
| Privilege tool | `setcap`/`getcap` (**libcap2-bin**) |
| Python libs | `python3-gi` (GTK), `python3-xapp`, `sqlite3`, `tkinter` |

### Conclusions
1. **Totals are trivial and exact** — no privilege, no daemon, no downloads.
2. **Per-app is affordable**: `nethogs` is tiny and its dependencies are normally already installed.
3. **Everything else is already installed** — no further downloads for the panel, storage, popup or graphs.
4. **Small RAM / disk** → avoid resident daemons; a **user** service is the right choice.

---

## 4. Recommended design

Two engines, one panel:

### 4.1 Engine 1 — Total (exact, zero-cost)

```
/proc/net/dev ──(read every 30s, free, no root)──► tiny script ──► writes today's total ──► panel "13.6 MB"
```

- Source of truth: kernel counters. Counts **all** interfaces except `lo`.
- Today's total = sum of deltas since midnight; auto-resets at date change; survives reboots (detects counter resets).
- Displayed value = today's combined **upload + download**.
- **Never touches the network. No root. No daemon.**

### 4.2 Engine 2 — Per-app (best-effort, small privilege)

```
nethogs (packet capture) ──► tiny Python parser ──► per-app daily totals ──► SQLite
        ▲                                                    │
   runs as a USER service                                     └──► popup list when you click the panel
   (one-time setcap grants packet-capture)
```

- `nethogs` maps live traffic to processes (covers **TCP + UDP/QUIC**, so video counts properly).
- A small parser converts its periodic output into per-app daily upload/download totals.
- Stored in the **same** tiny SQLite history (per-app table), pruned to 30 days.
- The **panel number still comes from Engine 1 (the exact total)**; per-app is shown only in the popup.

**Why nethogs and not a root-free method?** See the comparison:

| Engine | Privilege | Sees UDP/QUIC (video)? | Sees short-lived connections? | Covers system/other-user traffic | Download | RAM | Effort | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **nethogs** (packet capture) | one-time `setcap` capability, **no root daemon** | ✅ yes | mostly | only if run as a root service; otherwise shows **"Unknown/other"** | 34 KB | ~15–25 MB | Medium | ✅ **Recommended** |
| Root-free `ss` socket polling | none | ❌ **no** — UDP sockets have no byte counters (verified) | ❌ misses | no | 0 | ~10–20 MB | Low | ⚠️ Only if you refuse *any* privilege; will **undercount video/QUIC** |
| Custom eBPF | root + custom code | ✅ best | ✅ | yes | several MB (`bcc`) | low | Very high | ❌ Not now — too much to build/maintain |

> Note: with the recommended user-service setup, traffic from *other users / system services* (e.g. an OS update running as root) shows as one **"Unknown/system"** row rather than named. The **exact total still includes it**, so nothing is hidden. If you later want full naming, we can switch the collector to a sandboxed *system* service — a bigger step we should decide later.

### 4.3 What we reuse vs. build

| Responsibility | Decision | Source |
| --- | --- | --- |
| Read kernel counters | **Reuse** | Old `byte_counter.py` logic (no deps) |
| Today's delta + date rollover | **Build** (~60 lines) | New |
| Per-app parsing of nethogs | **Build** (small) | New |
| Daily + per-app storage (30-day prune) | **Reuse** pattern | Old `database.py` (SQLite, drop quota/approval tables) |
| Panel number | **Existing plugin** | `xfce4-genmon-plugin` |
| Popup list / later graph | **Existing libs** | `tkinter`/GTK (already installed) |
| Collector scheduling | **Existing** | systemd **user** service |

---

## 5. Panel & popup

- **In the panel:** `genmon` runs our script every 30 s and prints e.g. `▲▼ 13.6 MB`.
- **On click:** genmon runs a command that opens a small window with two tabs — **Today** (per-app
  usage: name, ↓, ↑, total) and **Chart** (a 30-day line graph you can filter to a single app).
- Your existing panel is **not reset or reconfigured** — we only *add* one item.

**Mock-up:**

```
  ┌────────────────────────────────────────────────────┐
  │  ▣ Menu   …   🔋  🔊  ▲▼ 13.6 MB   🕒 14:22        │  ← panel
  └────────────────────────────────────────────────────┘
                    ↓ click
  ┌──────────── Today's data ────────────┐
  │  Total: 13.6 MB   ↓ 11.2 MB  ↑ 2.4 MB│
  │  ────────────────────────────────────│
  │  brave          ↓ 9.8 MB   ↑ 1.1 MB  │
  │  opencode       ↓ 0.6 MB   ↑ 0.9 MB  │
  │  apt/system     ↓ 0.7 MB   ↑ 0.3 MB  │
  │  Unknown/other  ↓ 0.1 MB   ↑ 0.1 MB  │
  └──────────────────────────────────────┘
```

---

## 6. Resource budget (proposed targets)

| Resource | Phase 1 (total only) | Phase 2–3 (with per-app) |
| --- | --- | --- |
| RAM | ~0 MB resident | **~15–30 MB** (collector) + ~0 (panel) |
| CPU | < 0.05% avg | < 1% idle; scales with traffic (packet capture) |
| Disk | < 5 KB | < 250 KB for 30 days |
| Data used by the tool | **0 bytes** | **0 bytes** |
| One-time download | 52 KB (genmon) | + 34 KB (nethogs) |
| Startup impact | instant | small user service starts with login |

*Everything here is an estimate based on the tools' documented behaviour and the measured reference specs — we will replace estimates with measurements at milestone M4.*

---

## 7. Scope, build approach & file layout

The decision was to **build the total and per-app parts together**. Internally they are still *tested* in order (verify the exact total first, then layer per-app on top), because that catches mistakes early, but the finished bundle ships in one pass.

| Workstream | Feature | Difficulty | Downloads | Notes |
| --- | --- | --- | --- | --- |
| **W1** | Today's total in the panel | 🟢 Easy | 52 KB | Exact; from `/proc/net/dev`. |
| **W2** | Per-app collector (nethogs → SQLite) | 🟡 Medium | 34 KB + one-time `setcap` | The accurate, harder part. |
| **W3** | Click-to-open per-app popup | 🟢 Easy | 0 | Uses `tkinter`/GTK. |
| **W4** ✅ | 30-day history list + simple line graph (filter by app) | 🟢 Easy | 0 | Plain `tkinter` `Canvas`; no `matplotlib`. |

### 7.1 What was created (all inside `Linux_Data_Monitor/`)

```
Linux_Data_Monitor/
├── README.md                      ← install & usage guide (start here)
├── Read.md                        ← this plan, design notes and discoveries
├── bin/
│   ├── data-monitor               ← `today` / `apps` CLI (reads the database)
│   ├── dm-collector               ← runs nethogs, parses it, writes per-app totals
│   ├── dm-panel                   ← prints the panel text (called by genmon)
│   └── dm-popup                   ← the click-to-open per-app window (tkinter)
├── lib/
│   ├── counter.py                 ← totals from /proc/net/dev (adapted from old byte_counter.py)
│   ├── storage.py                 ← SQLite: daily + per-app, 30-day prune (from old database.py)
│   └── nethogs_parse.py           ← per-app parsing helpers
├── systemd/
│   └── data-monitor-collector.service   ← USER service (no root)
├── scripts/
│   ├── install-system.sh          ← sudo: packages + the one reversible setcap
│   ├── install-user.sh            ← no sudo: ~/.local/bin links + user service + panel item
│   ├── setup-panel.sh             ← add/repair (`--remove`) the panel item
│   └── uninstall.sh               ← cleanly reverses everything
└── tests/
    ├── test_counter.py
    ├── test_nethogs_parse.py
    └── test_storage.py
```

Run the tests any time with: `python3 -m unittest discover -s tests` (47 tests, no network, no writes
outside a temp directory).

### 7.2 The one-time setup commands actually used

```bash
sudo bash scripts/install-system.sh       # installs the two tiny packages + the reversible setcap
bash scripts/install-user.sh              # no sudo: tools, user service, and the panel item
```

No root daemon, no firewall, no changes to network configuration.

---

## 8. Accuracy and limitations (honest)

- ✅ **Total** is exact (kernel counters), includes all apps and system traffic, survives reboots and network changes.
- ⚠️ **Per-app** is best-effort:
  - Traffic from *other users / system services* appears as **"Unknown/system"** in the user-service setup (still counted in the total).
  - Very short-lived connections may be attributed imperfectly.
  - Bytes are tracked per **process** — nethogs' own label (program path + `pid`/`uid`, or a raw
    connection tuple) — so two unrelated counters are never merged into one running total. Only the
    *display* name is grouped, so several Brave processes still show up as a single `brave` row, correctly.
  - Per-app rows **will not sum exactly** to the total (that's expected; we show the difference as "Unknown" rather than hiding it).
- ⚠️ **First day only:** today's total starts counting from the moment the tool was first installed, so it
  may understate the first few hours of day one. Every following day is complete (and a mid-day reboot is
  handled: the counter reset is detected and counted from zero).
- ⚠️ **VPNs:** if one is added later, decide whether to count the VPN interface (to avoid double counting) — flagged for later.
- ✅ **Sanity check available:** a phone hotspot screen or a router counter gives an independent cross-check.

---

## 9. How the final product was reached (no coding required)

1. **Approve the plan** and answer Section 12.
2. **Phase 1 — totals only.** Built inside the project folder (no system changes). Run it and watch the number.
3. **Approve the small download(s).** The exact commands first: `xfce4-genmon-plugin` (~52 KB), later `nethogs` (~34 KB) + one `setcap` command.
4. **Add the panel item.** The existing panel is **not** reset — one item is *added*.
5. **Use the machine normally.** Within a refresh period the panel shows `▲▼ 13.6 MB` and updates itself.
6. **Phase 2–3:** the per-app collector and the click-popup are added.
7. **Removal is trivial:** remove the panel item, delete the scripts, disable the user service, `sudo setcap -r /usr/sbin/nethogs`. Nothing else was ever touched.

---

## 10. Roadmap (small, testable milestones)

| # | Milestone | Deliverable | Risk |
| --- | --- | --- | --- |
| M1 | Prove totals | Script prints today's total; cross-checked against an independent counter | 🟢 |
| M2 | Edge cases | Reboot mid-day, interface down/up, date rollover, counter reset | 🟢 |
| M3 | Panel integration | Number appears in panel, refreshes every 30 s | 🟢 |
| M4 | Measure resources | Confirm RAM/CPU/disk targets (replace estimates with readings) | 🟢 |
| M5 | Install/uninstall notes | Survives re-login; clean removal | 🟢 |
| M6 | Per-app collector | nethogs → user service → SQLite; verify with controlled traffic | 🟡 |
| M7 | Click popup | Per-app window from the panel item | 🟢 |
| M8 *(optional)* | History + graph | 30-day table + simple line graph | 🟡 |

Each milestone is reviewed with you before the next starts.

---

## 11. Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Panel layout disturbed | Low | Low | Only *add* an item; easily removed. |
| `setcap` misused / stale | Low | Low | One-time, reversible (`setcap -r`), scoped to nethogs only; no root daemon. |
| Per-app undercounts video | Low (nethogs covers UDP/QUIC) | Medium | We validate with controlled traffic; the exact total always backs it up. |
| Collector uses too much CPU on heavy traffic | Medium | Low | Tunable poll interval; user service can be paused; measure at M4/M6. |
| Counters reset on reboot | Certain | Low | Handled by design. |
| Scope creep back to the original "guardian" brief | Medium | High | Phases are explicit; Phase 4 is opt-in only. |
| Disk pressure (11 GB free) | Low | Low | Storage < 250 KB. |

---

## 12. Decisions — resolved, and the final gate

| # | Decision | Chosen |
| --- | --- | --- |
| 1 | Panel method | **`xfce4-genmon-plugin`** (52 KB) |
| 2 | Per-app engine | **`nethogs`** (accurate) |
| 3 | One-time setup (~86 KB + one `setcap`) | **Approved** |
| 4 | Build order | **Both together** |
| 5 | Reuse old code | **Yes** |

**Settings (defaults; open an issue to change them):** panel refresh every **60 s** · per-app grouped by **process name** · history retained **30 days** · popup window opened by clicking the panel number.

### ✅ Final gate — approved and completed

The plan was approved and the work completed. For the record, the order that was followed:

1. All code was built **inside the project folder** (no system changes).
2. The **exact total** was verified locally — it matches `/sys/class/net/<iface>/statistics` byte for byte.
3. The **per-app collector** was started as a user service (per-app rows await the one `setcap` in Section 0.2).
4. The approved packages were installed and the panel item was added.
5. Removal instructions are in Section 0.4.

Nothing outside the project folder, `~/.local`, `~/.config/xfce4` and the two packages was touched, and every
step is reversible.

---

## 13. Appendix

### 13.1 Commands used to inspect (all read-only, no downloads)
`cat /etc/os-release` · `uname -a` · `free -h` · `df -h` · `lscpu` · `xfce4-panel --version` · `dpkg -l` ·
`apt-cache policy/show xfce4-genmon-plugin nethogs libpcap0.8t64 libcap2-bin` · `ip -brief link/address` · `ip route` ·
`cat /proc/net/dev` · `ss -tunp` · `ss -tin` · `ss -uin` · Python import checks · `systemctl --user is-system-running`.

### 13.2 Known package sizes / availability (from local apt lists)
- `xfce4-genmon-plugin` 4.1.1-1build2 — **52,854 bytes** (.deb); installed ~370 KB; source `noble/universe`.
- `nethogs` 0.8.7-2build2 — **34,580 bytes** (.deb); installed ~89 KB; all deps already present.
- `libcap2-bin` (provides `setcap`) — **already installed**.

### 13.3 Glossary (plain English)
- **Panel / taskbar** — the bar with the clock (XFCE panel).
- **Plugin / applet** — a small widget in the panel (clock, battery, weather…).
- **`/proc/net/dev`** — a free, exact file the kernel provides listing bytes per network card.
- **`nethogs`** — a small tool that watches packets and says which process they belong to.
- **Packet capture / capability** — permission to look at network traffic; granted narrowly and reversibly with `setcap`.
- **Daemon** — a forever-running background program. We avoid a root one; the collector is a small **user** service.
- **SQLite** — one small file that stores structured data (our history).
- **MB / GB** — 1 GB ≈ 1024 MB.
- **QUIC/HTTP3** — the modern transport used by many video sites; it uses UDP, which is why `ss`-style methods miss it.

### 13.4 Note on filenames
`README.md` is the entry point (install & usage). `Read.md` is this design document.

⚠️ On case-insensitive filesystems (Windows, default macOS) `README.md` and `Read.md` are the **same
file**. If that matters for your setup, rename this document to `PLAN.md` and update the links in
`README.md`.
