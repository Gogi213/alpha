#!/usr/bin/env python3
"""Полнота и согласованность L1 на одном соединении (кадры cap.py с подпиской bbo-tbt + books5 + tickers + trades по одним символам):
  l1check.py F.jsonl.zst — сколько изменений верха книги видит bbo-tbt, сколько books5/tickers; для общих (символ, seqId) bbo-tbt и books5 сверяются цена и размер лучшего bid/ask; trades.seqId против bbo-tbt.seqId."""
import collections, io, json, sys
import zstandard

bbo, b5, tk, tr = {}, {}, collections.Counter(), {}
with open(sys.argv[1], "rb") as f:
    r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8")
    r.readline()
    for ln in r:
        d = json.loads(ln)
        if d["f"] == "pong":
            continue
        j = json.loads(d["f"])
        if "arg" not in j or "data" not in j:
            continue
        a = j["arg"]["channel"]
        for x in j["data"]:
            i = x.get("instId") or j["arg"]["instId"]
            if a == "bbo-tbt" and x["asks"] and x["bids"]:
                bbo[(i, x["seqId"])] = (x["bids"][0][0], x["bids"][0][1], x["asks"][0][0], x["asks"][0][1], int(x["ts"]))
            elif a == "books5":
                b5[(i, x["seqId"])] = (x["bids"][0][0], x["bids"][0][1], x["asks"][0][0], x["asks"][0][1], int(x["ts"]))
            elif a == "tickers":
                tk[i] += 1
            elif a == "trades":
                tr[(i, x["seqId"])] = 1
common = bbo.keys() & b5.keys()
same = sum(1 for k in common if bbo[k][:4] == b5[k][:4])
# изменения верха книги по bbo-tbt: сколько раз меняются (bid,bsz,ask,asz) подряд — считаем записи; books5 — то же по его снимкам
def changes(m):
    last, n = {}, 0
    for (i, s), v in sorted(m.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if last.get(i) != v[:4]:
            n += 1
        last[i] = v[:4]
    return n
tr_in_bbo = sum(1 for k in tr if k in bbo)
print(json.dumps({"bbo_updates": len(bbo), "books5_snapshots": len(b5), "tickers_msgs": sum(tk.values()), "bbo_distinct_L1_states": changes(bbo), "books5_distinct_L1_states": changes(b5),
                  "common_seqId": len(common), "common_same_L1": same, "common_diff_L1": len(common) - same,
                  "books5_seqId_not_in_bbo": len(b5.keys() - bbo.keys()), "trades_seqId": len(tr), "trades_seqId_in_bbo_seqIds": tr_in_bbo}))
