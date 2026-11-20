#!/usr/bin/env python3
"""Канал ticker Bitget: как часто на самом деле меняются поля. tickstats.py frames/P2-C.jsonl.zst > results/P2-C.tick.json
Для каждого символа последовательность кадров: интервал между кадрами (p50/p99 по всем), доля кадров, где изменилось: markPrice, indexPrice, fundingRate, bid/ask (цена, размер), любое из (markPrice, indexPrice, fundingRate), любое поле кроме ts; доля кадров с тем же ts."""
import collections, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
sys.path.insert(0, "/data/bitget-probe/g022-prep-3-tools")
import stats

last, n, ch = {}, 0, collections.Counter()
gaps = []
hdr, it = stats.lines(sys.argv[1])
for d in it:
    if "f" not in d or not d["f"].startswith("{"):
        continue
    j = json.loads(d["f"])
    if "data" not in j or j["arg"]["channel"] != "ticker":
        continue
    for x in j["data"]:
        s = x["instId"]
        p = last.get(s)
        n += 1
        if p is not None:
            gaps.append(int(x["ts"]) - int(p["ts"]))
            ch["same_ts"] += x["ts"] == p["ts"]
            for k in ("markPrice", "indexPrice", "fundingRate", "nextFundingTime"):
                ch[k] += x[k] != p[k]
            ch["bidask"] += (x["bidPr"], x["bidSz"], x["askPr"], x["askSz"]) != (p["bidPr"], p["bidSz"], p["askPr"], p["askSz"])
            ch["mark_idx_fund"] += (x["markPrice"], x["indexPrice"], x["fundingRate"]) != (p["markPrice"], p["indexPrice"], p["fundingRate"])
            ch["any_but_ts"] += {k: v for k, v in x.items() if k != "ts"} != {k: v for k, v in p.items() if k != "ts"}
        last[s] = x
m = n - len(last)
gaps.sort()
print(json.dumps({"file": sys.argv[1], "records": n, "pairs": m, "gap_ms_p50": gaps[len(gaps) // 2], "gap_ms_p99": gaps[int(len(gaps) * 0.99)], "changed_pct": {k: round(100 * v / m, 1) for k, v in ch.items()}, "keys": sorted(x.keys())}))
