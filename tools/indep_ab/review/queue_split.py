#!/usr/bin/env python3
"""TK-535 review: prod queue_lag_p99 split into before / clients only (before hold_* jobs) / clients + hold_*.

python3 queue_split.py > queue_split.txt
"""
import glob, json, statistics

W = {"A before 15:30-16:10": (1794843000, 1794845400),
     "B clients 16:21-16:58": (1794846060, 1794848280),
     "C clients+hold 17:01-19:05": (1794848460, 1794855900)}
ROOTS = {"bingx": "/data/collect/bingx/data", "okx": "/data/collect/okx/data", "gate": "/data/collect/gate/data",
         "bybit": "/data/turbo-probe/soak-bybit/data", "binance": "/data/collect/binance/data"}
print("exchange group | " + " | ".join(W) + "   (median over shards of the per-shard median queue_lag_p99_ms; n windows)")
for ex, root in ROOTS.items():
    groups = {}
    for f in sorted(glob.glob(root + "/stats-*.jsonl")):
        sh = f.split("stats-")[1][:-6]
        g = sh.rstrip("0123456789")
        rows = []
        for l in open(f, errors="replace").readlines()[-8000:]:
            try:
                d = json.loads(l)
            except ValueError:
                continue
            t = (d.get("ts_ms") or 0) // 1000
            if d.get("queue_lag_p99_ms") is None or t % 3600 <= 120 or t % 300 <= 20:
                continue
            rows.append((t, d["queue_lag_p99_ms"]))
        for name, (a, b) in W.items():
            v = [q for t, q in rows if a <= t < b]
            if v:
                groups.setdefault(g, {}).setdefault(name, []).append((statistics.median(v), len(v)))
    for g, per in sorted(groups.items()):
        cells = []
        for name in W:
            x = per.get(name, [])
            cells.append(f"{statistics.median(m for m, _ in x):.1f} (sh {len(x)}, n {sum(n for _, n in x)})" if x else "-")
        print(f"{ex} {g} | " + " | ".join(cells))

# host conditions in B and C from 1 Hz mpstat
DAY0 = 1794787200
st = {k: [] for k in W}
for line in open("/data/indep-ab/host/mpstat.log"):
    f = line.split()
    if len(f) == 12 and f[1] == "all":
        hh, mm, ss = map(int, f[0].split(":"))
        t = DAY0 + hh * 3600 + mm * 60 + ss
        for k, (a, b) in W.items():
            if a <= t < b and t % 3600 > 120 and t % 300 > 20:
                st[k].append((float(f[11]), float(f[8])))
for k, v in st.items():
    if v:
        print(f"host {k}: idle mean {statistics.mean(i for i, _ in v):.1f} steal mean {statistics.mean(s for _, s in v):.2f} n {len(v)}")
