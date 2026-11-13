#!/usr/bin/env python3
"""Раз в 30 с health всех рук последнего каталога shadow-прогона каждой биржи: armwatch.py <секунд> <биржа>...
Строка: время, биржа, рука, rx_clamp_us (n, p50, p99, p99.9, max за 60 с), totals.rx_clamped, rows, queue_lag p99, lag_out p50/p99, версия → /data/perf/armwatch.tsv"""
import glob, json, os, sys, time

secs = int(sys.argv[1])
exs = sys.argv[2:]
out = open("/data/perf/armwatch.tsv", "w")
out.write("t\texchange\tarm\tclamp_n\tp50\tp99\tp99_9\tmax\trx_clamped\trows\tqueue_lag_p99\tlag_out_p50\tlag_out_p99\tversion\n")
end = time.time() + secs
time.sleep(40)
while time.time() < end:
    for ex in exs:
        dirs = sorted(glob.glob(f"/data/perf/sh/{ex}-*"), key=os.path.getmtime)
        if not dirs:
            continue
        for arm in sorted(os.listdir(dirs[-1])):
            try:
                h = json.load(open(f"{dirs[-1]}/{arm}/health.json"))
            except Exception:
                continue
            c = h.get("rx_clamp_us") or {}
            t = h["totals"]
            out.write("\t".join(str(x) for x in (time.strftime("%H:%M:%S", time.gmtime()), ex, arm, c.get("n"), c.get("p50"), c.get("p99"), c.get("p99_9"), c.get("max"), t["rx_clamped"], t["rows"],
                                                   h["backpressure"]["queue_lag_ms"].get("p99"), h["lag_out_ms"].get("p50"), h["lag_out_ms"].get("p99"), h["process"]["version"])) + "\n")
    out.flush()
    time.sleep(30)
