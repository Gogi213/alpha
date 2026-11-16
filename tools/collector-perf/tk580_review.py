#!/usr/bin/env python3
"""TK-580 review: independent recompute of the BingX load-sensitivity and path-decision claims from raw srv files.
usage: python3 tk580_review.py > /data/tk580/review/out.txt   (systemd-run, Nice 19, IO idle)
Sources: /data/*/stats-*.jsonl (queue_lag_p99_ms, 10 s rows), sar -u (10 s), /data/indep-ab/host/{pidstat,psi}.log (1 Hz, 16.11 16:14-19:07),
events-*.jsonl of BingX, A/B copies /data/tk580/ab-w*/{old,new}/data/stats-*.jsonl."""
import collections, datetime, glob, json, os, statistics, subprocess

UTC = datetime.timezone.utc
ep = lambda *a: int(datetime.datetime(*a, tzinfo=UTC).timestamp())
T0, BUILD = ep(2026, 11, 14), ep(2026, 11, 16, 15, 26, 12)
W = {"A 15:30-16:10": (ep(2026, 11, 16, 15, 30), ep(2026, 11, 16, 16, 10)),
     "B 16:21-16:58": (ep(2026, 11, 16, 16, 21), ep(2026, 11, 16, 16, 58)),
     "C 17:01-19:05": (ep(2026, 11, 16, 17, 1), ep(2026, 11, 16, 19, 5))}
ROOTS = {"bingx": "/data/collect/bingx/data", "okx": "/data/collect/okx/data", "gate": "/data/collect/gate/data",
         "bybit": "/data/turbo-probe/soak-bybit/data"}
med = statistics.median
hm = lambda t: datetime.datetime.fromtimestamp(t, UTC).strftime("%d.%m %H:%M:%S")
skip = lambda t: t % 3600 <= 120 or t % 300 <= 20


def sar_idle():
    host = {}
    for day in range(13, 18):
        f = f"/var/log/sysstat/sa{day:02d}"
        if not os.path.exists(f):
            continue
        d0 = ep(2026, 11, day)
        out = subprocess.run(["sar", "-u", "-f", f], capture_output=True, text=True,
                             env={**os.environ, "LC_ALL": "C", "S_TIME_FORMAT": "ISO"}).stdout
        for l in out.splitlines():
            p = l.split()
            if len(p) == 8 and p[1] == "all":
                try:
                    hh, mm, ss = map(int, p[0].split(":"))
                except ValueError:
                    continue
                host[(d0 + hh * 3600 + mm * 60 + ss) // 10] = float(p[7])
    return host


def stats(root, t_from):
    rows = collections.defaultdict(list)  # kind -> [(t, shard, q)]
    for f in sorted(glob.glob(root + "/stats-*.jsonl")):
        sh = f.split("stats-")[1][:-6]
        for l in open(f, errors="replace"):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            t = (d.get("ts_ms") or 0) // 1000
            q = d.get("queue_lag_p99_ms")
            if q is None or t < t_from or skip(t):
                continue
            rows[sh.rstrip("0123456789")].append((t, sh, q))
    return rows


host = sar_idle()
idle_of = lambda t: next((host[k] for k in (t // 10, t // 10 - 1, t // 10 + 1) if k in host), None)
EDGES = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 101)]
data = {ex: stats(r, T0) for ex, r in ROOTS.items()}
print(f"# srv now {hm(int(datetime.datetime.now(UTC).timestamp()))} UTC; rows since {hm(T0)}, :00 (<=120 s) and 5-min (<=20 s) dropped")

print("\n## 1. TK-535 windows, full files (TK-535 read only the last 8000 lines): median over shards of per-shard medians | pooled-row median | n rows")
for ex in ROOTS:
    for kind, rows in sorted(data[ex].items()):
        cells = []
        for name, (a, b) in W.items():
            per = collections.defaultdict(list)
            for t, sh, q in rows:
                if a <= t < b:
                    per[sh].append(q)
            allq = [q for v in per.values() for q in v]
            cells.append(f"{med(med(v) for v in per.values()):5.1f} | {med(allq):5.1f} | n{len(allq)}" if per else "-")
        print(f"{ex} {kind}: " + " || ".join(cells))
for name, (a, b) in W.items():
    v = [idle_of(t) for t in range(a, b, 10) if not skip(t) and idle_of(t) is not None]
    print(f"host {name}: sar idle mean {statistics.mean(v):.1f}, share of 10-s bins idle<20 {sum(x < 20 for x in v) / len(v):.0%}, 20-35 {sum(20 <= x < 35 for x in v) / len(v):.0%}")

print("\n## 2. Idle-matched: window C vs all other rows since 14.11 (pooled 10-s rows, median / n)")
ca, cb = W["C 17:01-19:05"]
for ex in ROOTS:
    for kind, rows in sorted(data[ex].items()):
        cells = []
        for a, b in EDGES:
            inc = [q for t, sh, q in rows if ca <= t < cb and (i := idle_of(t)) is not None and a <= i < b]
            out = [q for t, sh, q in rows if not ca <= t < cb and (i := idle_of(t)) is not None and a <= i < b]
            cells.append(f"idle {a}-{b}: C {med(inc):5.1f} n{len(inc):4d} / rest {med(out):5.1f} n{len(out):5d}" if inc and out else f"idle {a}-{b}: -")
        print(f"{ex} {kind}: " + " | ".join(cells))

print("\n## 3. Hourly BingX book/mark/trade median vs OKX/Bybit book, all hours since 14.11 with BingX book >= 20 ms or host idle < 25 %")
hour = lambda rows: {h: med(v) for h, v in _group(rows).items()}


def _group(rows):
    g = collections.defaultdict(list)
    for t, sh, q in rows:
        g[t // 3600].append(q)
    return g


H = {(ex, k): hour(r) for ex in ROOTS for k, r in data[ex].items()}
hidle = collections.defaultdict(list)
for k10, v in host.items():
    hidle[k10 * 10 // 3600].append(v)
print("hour UTC | idle | bingx book mark trade | okx book | bybit book | gate book")
for h in sorted(H[("bingx", "book")]):
    b = H[("bingx", "book")][h]
    i = statistics.mean(hidle[h]) if hidle.get(h) else float("nan")
    if b >= 20 or i < 25:
        g = lambda ex, k: H.get((ex, k), {}).get(h, float("nan"))
        print(f"{hm(h * 3600)[:-3]} | {i:5.1f} | {b:5.1f} {g('bingx', 'mark'):5.1f} {g('bingx', 'trade'):5.1f} | {g('okx', 'book'):5.1f} | {g('bybit', 'book'):5.1f} | {g('gate', 'book'):5.1f}")
print("BingX book hourly median distribution since 14.11:", sorted(round(v, 1) for v in H[("bingx", "book")].values())[-12:], "(top 12)")
for lab, (a, b) in (("before build 29d981c0", (T0, BUILD)), ("after build", (BUILD, 1 << 40))):
    for a2, b2 in EDGES:
        v = [q for t, sh, q in data["bingx"]["book"] if a <= t < b and (i := idle_of(t)) is not None and a2 <= i < b2]
        print(f"bingx book {lab} idle {a2}-{b2}: median {med(v) if v else float('nan'):.1f} n {len(v)}")

print("\n## 4. Host composition at 1 Hz (pidstat/psi, 16.11 16:14-19:07): per window, mean %CPU by command group; BingX collector %CPU/%wait")
grp = collections.defaultdict(lambda: collections.defaultdict(float))
nsec = collections.Counter()
bx = collections.defaultdict(list)
day0 = ep(2026, 11, 16)
seen = set()
try:
    for l in open("/data/indep-ab/host/pidstat.log", errors="replace"):
        p = l.split(None, 13)
        if len(p) < 14 or not p[0][:2].isdigit() or p[2] == "PID":
            continue
        try:
            hh, mm, ss = map(int, p[0].split(":"))
            cpu, wait = float(p[7]), float(p[6])
        except ValueError:
            continue
        t = day0 + hh * 3600 + mm * 60 + ss
        name = next((n for n, (a, b) in W.items() if a <= t < b), None)
        if name is None or skip(t):
            continue
        if (name, t) not in seen:
            seen.add((name, t))
            nsec[name] += 1
        cmd = p[13].strip()
        if "collect run" in cmd and "bingx" in cmd and "tk580" not in cmd and "/data/collect/bingx" in cmd:
            bx[name].append((cpu, wait))
        key = ("collector " + cmd.split("--exchange ")[1].split()[0]) if "collect run" in cmd and "--exchange " in cmd else \
              "hold_* (python hold_)" if "hold_" in cmd else "zstd" if cmd.startswith("zstd") else "mawk" if "mawk" in cmd else \
              "cargo/rustc" if ("rustc" in cmd or "cargo" in cmd) else "python other" if cmd.startswith("python") else \
              "ksoftirqd" if "ksoftirqd" in cmd else "other"
        grp[name][key] += cpu
    for name in W:
        if not nsec[name]:
            continue
        tops = sorted(((v / nsec[name], k) for k, v in grp[name].items()), reverse=True)[:9]
        b = bx.get(name) or [(float("nan"), float("nan"))]
        print(f"{name}: s {nsec[name]}; " + ", ".join(f"{k} {v:.0f}" for v, k in tops) +
              f" | bingx collector %CPU {statistics.mean(c for c, _ in b):.1f} %wait {statistics.mean(w for _, w in b):.1f} (n {len(bx.get(name, []))})")
except FileNotFoundError:
    print("no pidstat.log")
try:
    ps = collections.defaultdict(lambda: collections.defaultdict(list))
    for l in open("/data/indep-ab/host/psi.log", errors="replace"):
        parts = l.split("|")
        try:
            t = int(float(parts[0].split()[0]))
        except (ValueError, IndexError):
            continue
        name = next((n for n, (a, b) in W.items() if a <= t < b), None)
        if name is None:
            continue
        for i, lab in enumerate(("cpu", "io", "mem"), 1):
            if i < len(parts) and "avg10=" in parts[i]:
                ps[name][lab].append(float(parts[i].split("avg10=")[1].split()[0]))
    for name, d in ps.items():
        print(f"psi {name}: " + ", ".join(f"{k} some avg10 mean {statistics.mean(v):.1f}" for k, v in d.items()) + "  (column order as in psi.log: cpu | io | memory — assumed)")
except FileNotFoundError:
    print("no psi.log")

print("\n## 5. BingX path decisions since build 29d981c0 to now: switch causes by time pairing (same shard, ping_degraded of old_session within 0-180 s before)")
ev = []
for f in glob.glob("/data/collect/bingx/data/events-*.jsonl"):
    sh = f.split("events-")[1][:-6]
    for l in open(f, errors="replace"):
        try:
            d = json.loads(l)
        except ValueError:
            continue
        d.setdefault("shard", sh)
        ev.append(d)
ev.sort(key=lambda d: d.get("ts_ms", 0))
for lab, a in (("whole log", 0), ("since build", BUILD * 1000)):
    sel = [d for d in ev if d.get("ts_ms", 0) >= a]
    c = collections.Counter(d["ev"] for d in sel)
    print(f"{lab}: {hm(sel[0]['ts_ms'] // 1000)} .. {hm(sel[-1]['ts_ms'] // 1000)}:",
          {k: v for k, v in sorted(c.items()) if k.startswith(("path", "ping", "switch", "upgrade", "replace", "rotated", "timeout"))})
    pdg = collections.defaultdict(list)
    for d in sel:
        if d["ev"] == "ping_degraded":
            pdg[(d["shard"], d.get("session"))].append(d["ts_ms"])
    dts, miss = [], []
    for d in sel:
        if d["ev"] != "path_switch":
            continue
        prior = [x for x in pdg.get((d["shard"], d.get("old_session")), []) if 0 <= d["ts_ms"] - x <= 180_000]
        if prior:
            dts.append((d["ts_ms"] - max(prior)) / 1000)
        else:
            miss.append((hm(d["ts_ms"] // 1000), d["shard"]))
    if dts:
        print(f"  path_switch with ping_degraded 0-180 s before: {len(dts)}; delay s min/med/max {min(dts):.1f}/{med(dts):.1f}/{max(dts):.1f}; without: {len(miss)} {miss[:10]}")
    for d in sel:
        if d["ev"] == "path_rejected":
            print("  path_rejected", hm(d["ts_ms"] // 1000), d["shard"], d.get("p50_ms"), d.get("p99_ms"), d.get("n"))
    for kind in ("book", "trade", "mark"):
        p = sorted(d["p50_ms"] for d in sel if d["ev"] == "path_ok" and d["shard"].startswith(kind) and d.get("p50_ms") is not None)
        if p:
            print(f"  path_ok {kind}: n {len(p)} p50 median {med(p):.0f} max {p[-1]:.0f} (limit 350)")

print("\n## 6. A/B copies: per-window per-copy queue_lag_p99 rows > 100 ms (stall tails), by shard")
for D in sorted(glob.glob("/data/tk580/ab-w*")):
    for n in ("old", "new", "new2", "rep"):
        fs = glob.glob(f"{D}/{n}/data/stats-*.jsonl")
        if not fs:
            continue
        allq, big = [], collections.Counter()
        for f in fs:
            sh = f.split("stats-")[1][:-6]
            rows = [json.loads(l) for l in open(f) if l.startswith("{")]
            q = [r["queue_lag_p99_ms"] for r in rows if r.get("queue_lag_p99_ms") is not None]
            allq += q
            big[sh] = sum(x > 100 for x in q)
        if allq:
            print(f"{os.path.basename(D)} {n}: rows {len(allq)}, median {med(allq):.1f}, rows >100 ms {sum(big.values())} by shard {dict(big)}, max {max(allq):.0f}")
