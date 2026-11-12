#!/usr/bin/env python3
"""Хвост лага по входам (кадры cap.py): записи bbo-tbt с rx−ts > порога — сколько, во сколько 10-секундных корзин от старта они попали, макс. лаг.
  tail.py THRESHOLD_MS F1.jsonl.zst [F2 ...]  — по файлу: n записей, n поздних, корзин с поздними / всего, самая длинная серия подряд идущих корзин, макс. лаг; затем совпадение корзин между файлами."""
import collections, json, sys
from stats import lines

thr = float(sys.argv[1])
buckets = {}
for p in sys.argv[2:]:
    hdr, it = lines(p)
    n = late = 0
    mx = 0
    b = collections.Counter()
    t0 = None
    tot = set()
    for d in it:
        if d["f"] == "pong":
            continue
        j = json.loads(d["f"])
        if "arg" not in j or j["arg"]["channel"] != "bbo-tbt":
            continue
        t0 = t0 or d["rx_ns"]
        k = int((d["rx_ns"] - t0) / 1e10)
        tot.add(k)
        if (d["rx_ns"] - t0) < 5e9:
            continue
        for x in j["data"]:
            lag = d["rx_ns"] // 1_000_000 - int(x["ts"])
            n += 1
            if lag > thr:
                late += 1
                b[k] += 1
            mx = max(mx, lag)
    ks = sorted(b)
    run = best = 0
    prev = None
    for k in ks:
        run = run + 1 if prev is not None and k == prev + 1 else 1
        best = max(best, run)
        prev = k
    buckets[p] = set(ks)
    print(json.dumps({"file": p, "peer": hdr["peer"], "bbo_records": n, "late": late, "late_share_pct": round(100 * late / n, 2), "buckets_with_late": len(ks), "buckets_total": len(tot), "longest_run_buckets": best, "lag_max": mx}))
names = list(buckets)
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        a, b2 = buckets[names[i]], buckets[names[j]]
        print(json.dumps({"pair": [names[i], names[j]], "common_late_buckets": len(a & b2), "only_first": len(a - b2), "only_second": len(b2 - a)}))
