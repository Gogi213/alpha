#!/usr/bin/env python3
"""Сделки Bitget: совпадают ли v2 `trade` (tradeId) и UTA v3 `publicTrade` (i, L) за одно и то же окно на одних символах; что такое L − i; доля isRPI; первые 3 кадра каждого вида.
  tradeids.py frames/T1-C-v3publictrade100.jsonl.zst frames/T1-D-v2trade100.jsonl.zst > results/T1-tradeids.json"""
import collections, json, os, sys

sys.path.insert(0, "/data/bitget-probe/g022-prep-3-tools")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
import stats

v3 = {}
dl = collections.Counter()
rpi = collections.Counter()
hdr, it = stats.lines(sys.argv[1])
t0 = hdr["rx_ns"]
for d in it:
    if "f" not in d or not d["f"].startswith("{"):
        continue
    j = json.loads(d["f"])
    if j.get("action") != "update":
        continue
    for x in j["data"]:
        v3[(j["arg"]["symbol"], x["i"])] = x
        dl[int(x["L"]) - int(x["i"])] += 1
        rpi[x["isRPI"]] += 1
v2 = {}
hdr2, it = stats.lines(sys.argv[2])
for d in it:
    if "f" not in d or not d["f"].startswith("{"):
        continue
    j = json.loads(d["f"])
    if j.get("action") != "update":
        continue
    for x in j["data"]:
        v2[(j["arg"]["instId"], x["tradeId"])] = x
lo = max(hdr["rx_ns"], hdr2["rx_ns"]) // 1_000_000 + 5000
hi = min(hdr["rx_ns"], hdr2["rx_ns"]) // 1_000_000 + 85000
w3 = {k: x for k, x in v3.items() if lo <= int(x["T"]) <= hi}
w2 = {k: x for k, x in v2.items() if lo <= int(x["ts"]) <= hi}
both = w3.keys() & w2.keys()
mism = sum(1 for k in both if (w3[k]["p"], w3[k]["v"], w3[k]["S"], w3[k]["T"]) != (w2[k]["price"], w2[k]["size"], w2[k]["side"], w2[k]["ts"]))
print(json.dumps({"v3_updates": len(v3), "v2_updates": len(v2), "window_ms": [lo, hi], "v3_in_window": len(w3), "v2_in_window": len(w2), "common_id": len(both), "only_v3": len(w3.keys() - w2.keys()), "only_v2": len(w2.keys() - w3.keys()), "value_mismatch_on_common": mism,
                  "L_minus_i": dict(dl.most_common(6)), "isRPI": dict(rpi), "v3_sample": list(v3.values())[:2], "v2_sample": list(v2.values())[:2]}))
