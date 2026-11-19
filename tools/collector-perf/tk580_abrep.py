#!/usr/bin/env python3
"""TK-580 A/B report for one window: python3 abrep.py /data/tk580/ab-<label>"""
import json, re, sys, glob, statistics
D = sys.argv[1]
def num(s): return float(s.replace(",", "").replace(" ", ""))
def perf(f):
    t = open(f).read(); r = {}
    for k, pat in (("task_ms", r"([\d,\.]+)\s+msec task-clock"), ("cycles", r"([\d,]+)\s+cycles"), ("instr", r"([\d,]+)\s+instructions")):
        m = re.search(pat, t); r[k] = num(m.group(1)) if m else None
    return r
out = {}
for n in ("old", "new"):
    p = perf(f"{D}/{n}.perfstat.txt")
    s0 = list(map(int, open(f"{D}/{n}.ss0").read().split())); s1 = list(map(int, open(f"{D}/{n}.ss1").read().split()))
    h0 = json.load(open(f"{D}/{n}.h0.json")); h1 = json.load(open(f"{D}/{n}.h1.json"))
    dt = (h1["ts_ms"] - h0["ts_ms"]) / 1000
    fr = h1["reactor"]["frames"] - h0["reactor"]["frames"]
    run_ms = (s1[0] - s0[0]) / 1e6; wait_ms = (s1[1] - s0[1]) / 1e6
    rows = []
    t_from = h0["ts_ms"]
    for f in glob.glob(f"{D}/{n}/data/stats-*.jsonl"):
        for l in open(f):
            try: d = json.loads(l)
            except ValueError: continue
            if d.get("ts_ms", 0) >= t_from and d.get("ts_ms", 0) <= h1["ts_ms"] and d.get("queue_lag_p99_ms") is not None:
                rows.append(d["queue_lag_p99_ms"])
    rows.sort()
    q = lambda p: rows[min(len(rows) - 1, int(len(rows) * p))] if rows else None
    o = dict(window_s=round(dt), frames=fr, fps=round(fr / dt), task_ms=p["task_ms"], on_cpu_pct=round(p["task_ms"] / dt / 10, 1),
             cycles_per_frame=round(p["cycles"] / fr), instr_per_frame=round(p["instr"] / fr), ipc=round(p["instr"] / p["cycles"], 2),
             ghz_eff=round(p["cycles"] / p["task_ms"] / 1e6, 3), oncpu_us_per_frame=round(p["task_ms"] * 1000 / fr, 1),
             runq_wait_ms_per_s=round(wait_ms / dt, 1), slices=s1[2] - s0[2],
             queue_lag_p99_ms_median=q(0.5), queue_lag_p99_ms_p90=q(0.9), n_rows=len(rows),
             loop_us=h1["reactor"]["loop_us"], tick_us=h1["reactor"]["worker_tick_us"], gzip=h1.get("gzip"), version=h1["process"]["version"],
             cpu_cores=h1["process"]["cpu_cores"], sched_wait_cores=h1["process"]["sched_wait_cores"])
    out[n] = o
    print(n, json.dumps(o))
# host
idle = []; steal = []
for l in open(f"{D}/mpstat.txt"):
    f = l.split()
    if len(f) == 12 and f[1] == "all":
        try: idle.append(float(f[11])); steal.append(float(f[8]))
        except ValueError: pass
print("host idle mean %.1f steal mean %.2f n %d" % (statistics.mean(idle), statistics.mean(steal), len(idle)))
a, b = out["old"], out["new"]
for k in ("cycles_per_frame", "instr_per_frame", "oncpu_us_per_frame", "queue_lag_p99_ms_median", "queue_lag_p99_ms_p90"):
    print(f"{k}: old {a[k]} new {b[k]} ratio new/old {b[k]/a[k]:.3f}")
