#!/usr/bin/env python3
"""Сколько разных состояний верха книги (bid, bsz, ask, asz) отдаёт каждый канал на одних и тех же символах за окно; согласованность по общим seq.
  states.py frames/P1-D.jsonl.zst > results/P1-D.states.json
books1 / books5 (верх = asks[0], bids[0]) / ticker (bidPr, bidSz, askPr, askSz). Для books1 и books5 — число общих (символ, seq) и расхождений верха между ними."""
import collections, json, os, sys

sys.path.insert(0, "/data/bitget-probe/g022-prep-3-tools")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
import stats

last, cnt, rec, tops = {}, collections.Counter(), collections.Counter(), {}
hdr, it = stats.lines(sys.argv[1])
first = None
for d in it:
    if "f" not in d or not d["f"].startswith("{"):
        continue
    j = json.loads(d["f"])
    if "data" not in j:
        continue
    first = first or d["rx_ns"]
    if d["rx_ns"] - first < 5e9:
        continue
    a_ = j["arg"]
    ch, sym = a_.get("channel") or a_.get("topic"), a_.get("instId") or a_.get("symbol")
    for x in j["data"]:
        if ch in ("books1", "books5"):
            bids, asks = x.get("bids") or x["b"], x.get("asks") or x["a"]
            st = (bids[0][0], bids[0][1], asks[0][0], asks[0][1])
            tops[(ch, sym, x["seq"])] = st
        elif ch == "ticker":
            st = (x["bidPr"], x["bidSz"], x["askPr"], x["askSz"]) if "bidPr" in x else (x["bid1Price"], x["bid1Size"], x["ask1Price"], x["ask1Size"])
        else:
            continue
        rec[ch] += 1
        if last.get((ch, sym)) != st:
            cnt[ch] += 1
        last[(ch, sym)] = st
common = [(k, tops[k], tops.get(("books5",) + k[1:])) for k in tops if k[0] == "books1" and ("books5",) + k[1:] in tops]
bad = sum(1 for k, a, b in common if a != b)
print(json.dumps({"file": sys.argv[1], "records": dict(rec), "distinct_top_states": dict(cnt), "books1_vs_books5_common_seq": len(common), "mismatch": bad}))
