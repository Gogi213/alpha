#!/usr/bin/env python3
"""Подписки пробы из пула: plan.py POOL.json N OUT_DIR
  N > 0: топ-N по обороту — A (bbo-tbt+trades), B (mark-price+funding-rate+index-tickers), E (bbo-tbt+books5+tickers+trades топ-10, для проб входов)
  N = 0: весь пул — F-book (bbo-tbt), F-trade (trades), F-mark (mark-price+funding-rate+index-tickers): по одному соединению на вид"""
import json, sys
p = json.load(open(sys.argv[1])); n = int(sys.argv[2]); d = sys.argv[3]
W = lambda name, args: json.dump(args, open(f"{d}/{name}.json", "w"))
if n > 0:
    top = list(p["qv"])[:n]
    idx = sorted({s[: -len("-SWAP")] for s in top})
    W("subs-A", [{"channel": c, "instId": s} for s in top for c in ("bbo-tbt", "trades")])
    W("subs-B", [{"channel": "mark-price", "instId": s} for s in top] + [{"channel": "funding-rate", "instId": s} for s in top] + [{"channel": "index-tickers", "instId": i} for i in idx])
    W("subs-E", [{"channel": c, "instId": s} for s in top[:10] for c in ("bbo-tbt", "books5", "tickers", "trades")])
    print(len(top), len(idx))
else:
    al = list(p["pool"])
    idx = sorted({s[: -len("-SWAP")] for s in al})
    W("subs-F-book", [{"channel": "bbo-tbt", "instId": s} for s in al])
    W("subs-F-trade", [{"channel": "trades", "instId": s} for s in al])
    W("subs-F-mark", [{"channel": "mark-price", "instId": s} for s in al] + [{"channel": "funding-rate", "instId": s} for s in al] + [{"channel": "index-tickers", "instId": i} for i in idx])
    print(len(al), len(idx))
