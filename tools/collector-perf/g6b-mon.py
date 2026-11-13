#!/usr/bin/env python3
"""Монитор G6b: раз в 30 с queue_lag боевых коллекторов и теневых рук, PSI cpu.
Первые BASE_S секунд — фон: пороги боя на биржу = max(10 мс, 2 × медиана фона). Затем два замера подряд выше порога на любой бирже —
стоп всех теневых процессов (shadow-*), g6b.done = ABORTED. Руки пишутся в g6b-arms.tsv (время, биржа, рука, p50, p99, p99_9, lag_out p99)."""
import glob, json, os, statistics, subprocess, sys, time

BASE_S = int(sys.argv[1]) if len(sys.argv) > 1 else 300
PROD = {
    "binance": "/data/turbo-probe/soak-binance/health.json",
    "bybit": "/data/turbo-probe/soak-bybit/health.json",
    "okx": "/data/collect/okx/health.json",
    "bingx": "/data/collect/bingx/health.json",
}
LOG, ARMS, DONE = "/data/perf/g6b-mon.txt", "/data/perf/g6b-arms.tsv", "/data/perf/g6b.done"


def health(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def psi():
    for ln in open("/proc/pressure/cpu"):
        if ln.startswith("some"):
            return ln.split(" total")[0].strip()
    return "?"


def log(s):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%T', time.gmtime())} {s}\n")


t0 = time.time()
base = {e: [] for e in PROD}
thr = None
bad = 0
open(ARMS, "w").write("t\texchange\tarm\tp50\tp99\tp99_9\tlag_out_p99\n")
while not os.path.exists(DONE):
    now = time.time()
    cur = {}
    for e, p in PROD.items():
        h = health(p)
        try:
            cur[e] = h["backpressure"]["queue_lag_ms"]["p99"]
        except Exception:
            cur[e] = None
    if thr is None and now - t0 >= BASE_S:
        thr = {e: max(10.0, 2 * statistics.median(v)) if v else 10.0 for e, v in base.items()}
        log("пороги боя (мс): " + " ".join(f"{e}={t:.1f}" for e, t in thr.items()))
    if thr is None:
        for e, v in cur.items():
            if v is not None:
                base[e].append(v)
    over = [e for e, v in cur.items() if thr is not None and v is not None and v > thr[e]]
    bad = bad + 1 if over else 0
    log(" ".join(f"{e}={v}" for e, v in cur.items()) + f" | {psi()} | load {open('/proc/loadavg').read().split()[0]}" + (" | ФАЗА ФОНА" if thr is None else ""))
    if thr is not None:
        for e in PROD:
            dirs = sorted(glob.glob(f"/data/perf/sh/{e}-*"), key=os.path.getmtime)
            if not dirs:
                continue
            for arm in sorted(os.listdir(dirs[-1])):
                h = health(f"{dirs[-1]}/{arm}/health.json") if os.path.isdir(f"{dirs[-1]}/{arm}") else None
                if not h:
                    continue
                try:
                    q = h["backpressure"]["queue_lag_ms"]
                    with open(ARMS, "a") as f:
                        f.write(f"{int(now)}\t{e}\t{arm}\t{q.get('p50')}\t{q.get('p99')}\t{q.get('p99_9')}\t{h['lag_out_ms'].get('p99')}\n")
                except Exception:
                    pass
    if bad >= 2:
        log(f"СТОП: боевой queue_lag p99 выше порога два замера подряд ({', '.join(f'{e}={cur[e]}>{thr[e]:.1f}' for e in over)})")
        units = subprocess.run("systemctl list-units 'shadow-*' --no-legend | awk '{print $1}'", shell=True, capture_output=True, text=True).stdout.split()
        for u in units:
            subprocess.run(["systemctl", "stop", u])
        open(DONE, "w").write("ABORTED\n")
        sys.exit(1)
    time.sleep(30)
