#!/usr/bin/env python3
"""Bybit: пишет события pause_check и gap_end шардов (events-*.jsonl нового процесса) в pause_check-bybit.jsonl по мере появления."""
import glob, json, os, time
ROOT = "/data/turbo-probe/soak-bybit/data"; OUT = "/data/perf/next-deploy/pause_check-bybit.jsonl"; SINCE = 1794828074000
pos = {}
while True:
    for f in sorted(glob.glob(os.path.join(ROOT, "events-*.jsonl"))):
        try:
            with open(f, "rb") as fh:
                fh.seek(pos.get(f, 0)); data = fh.read(); end = data.rfind(b"\n") + 1; pos[f] = pos.get(f, 0) + end
        except OSError:
            continue
        for ln in data[:end].splitlines():
            if b"pause_check" in ln or b"gap_end" in ln or b"\"ev\":\"gap\"" in ln:
                try: d = json.loads(ln)
                except ValueError: continue
                if d.get("ts_ms", 0) >= SINCE:
                    with open(OUT, "a") as o: o.write(json.dumps(d, ensure_ascii=False) + "\n")
    time.sleep(5)
