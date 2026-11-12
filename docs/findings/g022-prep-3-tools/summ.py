#!/usr/bin/env python3
"""Сводка проб: summ.py EX PREFIX OUTDIR — для каждого frames/PREFIX*.jsonl.zst пишет OUTDIR/<имя>.stats.json (stats.one) и печатает компактно:
соединение, пир, connect_s, окно, события/обрыв, по каналам: кадров/с, записей/с, символов, лаг p50/p99/max, доля >250 мс, повторы/откаты ключа."""
import glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stats

ex, prefix, outdir = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)
for p in sorted(glob.glob(f"frames/{prefix}*.jsonl.zst")):
    d = stats.one(ex, p)
    name = os.path.basename(p).replace(".jsonl.zst", "")
    json.dump(d, open(f"{outdir}/{name}.stats.json", "w"), ensure_ascii=False)
    print(name, "peer", d["hdr"].get("peer"), "connect_s", d["hdr"].get("connect_s"), "dur", d["dur_s"], "tot fps", d["total_frames_per_s"], "kB/s", d["total_kb_per_s"], "events", d["events"], "closed", d["closed"])
    for c, v in d["channels"].items():
        print("   ", c, {x: v[x] for x in ("frames_per_s", "recs_per_s", "symbols", "lag_p50", "lag_p99", "lag_max", "lag_gt250_pct", "push_minus_E_p50", "push_minus_E_p99", "gt250_pct_by_10s", "key_dup", "key_back", "flags")})
