#!/usr/bin/env python3
"""TK-580 W3 report: python3 abrep2.py /data/tk580/ab-w3 — copies A (Nice 0) and B (Nice -10) of the same binary, phases P0 (quiet) / P1 (own nice-19 load)."""
import json, sys, glob, statistics, datetime
D = sys.argv[1]
def ss(n, t):
    f = open(f"{D}/{n}.{t}.ss").read().split(); return dict(ts=float(f[0]), run=int(f[1]), wait=int(f[2]), slices=int(f[3]), vol=int(f[4]), nonvol=int(f[5]))
def hj(n, t): return json.load(open(f"{D}/{n}.{t}.h.json"))
day0 = datetime.datetime.fromtimestamp(ss("A", "t1")["ts"], datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
mp = []
for l in open(f"{D}/mpstat.txt"):
    f = l.split()
    if len(f) == 12 and f[1] == "all":
        try: hh, mm, s = map(int, f[0].split(":")); mp.append((day0 + hh * 3600 + mm * 60 + s, float(f[11]), float(f[8]), float(f[3]), float(f[4])))
        except ValueError: pass
def rows(n, t_from_ms, t_to_ms):
    r = []
    for f in glob.glob(f"{D}/{n}/data/stats-*.jsonl"):
        for l in open(f):
            try: d = json.loads(l)
            except ValueError: continue
            if t_from_ms <= d.get("ts_ms", 0) < t_to_ms and d.get("queue_lag_p99_ms") is not None: r.append(d["queue_lag_p99_ms"])
    return sorted(r)
PH = (("P0 quiet", ("t1", "t2")), ("P1 memory bandwidth hog nice19", ("t2", "t3")), ("P2 memory latency hog nice19", ("t3", "t4")))
for ph, (a, b) in PH:
    t0 = ss("A", a)["ts"]; t1 = ss("A", b)["ts"]
    m = [x for x in mp if t0 <= x[0] < t1]
    print(f"== {ph}: {t1 - t0:.0f} s, host idle {statistics.mean(x[1] for x in m):.1f}% steal {statistics.mean(x[2] for x in m):.2f}% nice {statistics.mean(x[3] for x in m):.1f}% sys {statistics.mean(x[4] for x in m):.1f}%")
    for n, label in (("A", "old        "), ("B", "new        "), ("C", "new (repl.)")):
        s0, s1 = ss(n, a), ss(n, b); h0, h1 = hj(n, a), hj(n, b)
        dt = s1["ts"] - s0["ts"]; fr = h1["reactor"]["frames"] - h0["reactor"]["frames"]
        r = rows(n, h0["ts_ms"], h1["ts_ms"]); q = lambda p: r[min(len(r) - 1, int(len(r) * p))]
        print(f"  {label}: frames/s {fr / dt:.0f}, on-CPU {(s1['run'] - s0['run']) / dt / 1e7:.1f}%, runq wait {(s1['wait'] - s0['wait']) / dt / 1e6:.1f} ms/s, "
              f"wakeups/s {(s1['slices'] - s0['slices']) / dt:.0f}, involuntary sw/s {(s1['nonvol'] - s0['nonvol']) / dt:.1f}, "
              f"queue_lag_p99 median {q(.5):.1f} p90 {q(.9):.1f} max {r[-1]:.1f} (n {len(r)})")
