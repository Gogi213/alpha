#!/usr/bin/env python3
"""TK-581: для набора окон — что видят независимые показатели: плохие секунды netwatch (>=2 из 4 целей), события коллекторов timeout / ping_degraded / gap / path_rejected_rtt по биржам,
минимальная доля сообщений (msgs слота / медиана окна 21:00–22:30) по биржам и хост (steal, sys, idle по sar-CSV, если передан --host).
python3 win_check.py --host host10s.csv  name=HH:MM:SS-HH:MM:SS  [...]   (на srv: без --host читает только netwatch/events/stats)"""
import json, sys, glob, collections, statistics as st, csv, datetime as dt
d0 = dt.datetime.strptime("2026-11-16", "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
h = lambda s: int(d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:]))
args = sys.argv[1:]
host = None
if "--host" in args:
    i = args.index("--host"); host = args[i + 1]; del args[i:i + 2]
wins = []
for a in args:
    n, r = a.split("=")
    lo, hi = r.split("-")
    wins.append((n, h(lo), h(hi)))
AB = {"binance": "BN", "bybit": "BB", "okx": "OK", "bingx": "BX", "gate": "GT"}
TCP = ["binance", "bybit", "okx", "bingx"]
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
# netwatch
sec = collections.defaultdict(dict)
for line in open("/data/netwatch/2026-11-16.jsonl"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if "ts" in o and h("20:30:00") <= o["ts"] < h("23:00:00"):
        sec[int(o["ts"])][o["n"]] = o["rtt"]
def nbad(lo, hi):
    c = 0
    for s in range(lo, hi + 1):
        k = 0
        for n in TCP:
            v = sec[s].get(n, "missing")
            if v == "missing" or v is None or v >= (1500 if n == "binance" else 1000):
                k += 1
        c += k >= 2
    return c
# events
EV = collections.defaultdict(list)  # ex -> [(t, ev)]
for ex, root in ROOTS.items():
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            if '"ts_ms"' not in line:
                continue
            if not any(k in line for k in ('"timeout"', '"ping_degraded"', '"gap"', '"path_rejected_rtt"')):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o["ts_ms"] / 1000
            if h("20:30:00") <= t < h("23:00:00"):
                EV[ex].append((t, o["ev"]))
# stats: msgs по слотам
MS = {}
for ex, root in ROOTS.items():
    slots = collections.defaultdict(int)
    for p in glob.glob(root + "/stats-*.jsonl"):
        for line in open(p):
            if '"ts_ms"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o["ts_ms"] / 1000
            if h("21:00:00") <= t < h("22:30:00"):
                slots[int(t // 10) * 10] += o.get("msgs") or 0
    med = st.median(slots.values()) if slots else 1
    MS[ex] = (slots, med)
H = []
if host:
    H = list(csv.DictReader(open(host)))
print("окно | длит. с | netwatch плохих с | события (timeout+ping_degraded+gap+path_rejected_rtt) по биржам | мин. msgs/медиана по биржам | хост steal% / sys% / idle% (среднее, макс.)")
for n, lo, hi in wins:
    evs = {ex: sum(1 for t, e in EV[ex] if lo <= t <= hi) for ex in ROOTS}
    mn = {}
    for ex in ROOTS:
        slots, med = MS[ex]
        v = [slots[s] / med for s in range(lo // 10 * 10, hi // 10 * 10 + 1, 10) if s in slots]
        mn[ex] = min(v) if v else float("nan")
    hs = ""
    if H:
        def hv(k):
            xs = [float(r[k]) for r in H if lo <= h(r["t"]) <= hi]
            return (st.mean(xs), max(xs)) if xs else (float("nan"), float("nan"))
        s1, s2, s3 = hv("cpu_steal"), hv("cpu_sys"), hv("cpu_idle")
        hs = f"steal {s1[0]:.1f}/{s1[1]:.1f}  sys {s2[0]:.1f}/{s2[1]:.1f}  idle {s3[0]:.1f}/{s3[1]:.1f}"
    print(f"{n} | {hi-lo+1} | {nbad(lo, hi)} | " + " ".join(f"{AB[ex]}={evs[ex]}" for ex in ROOTS) + " | " + " ".join(f"{AB[ex]}={mn[ex]:.2f}" for ex in ROOTS) + " | " + hs)
