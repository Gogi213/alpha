#!/usr/bin/env python3
"""Файлы подписок для cap.py по пулу (pool-DAY.json, qv отсортирован по обороту):
  plan.py EX --pool pool.json --top N --kinds k1,k2 --out subs.json [--batch 40]
EX: bitget (kinds — каналы: books1,books5,trade,ticker), htx (kinds — bbo,trade.detail,detail,mark_price,funding_rate…), bingx (kinds — bookTicker,trade,markPrice…).
Bitget — сообщения {"op":"subscribe","args":[…]} по batch аргументов; HTX/BingX — по одному сообщению на (символ, вид)."""
import argparse, json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ex")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--top", type=int, default=10**9)
    ap.add_argument("--kinds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--from-rank", type=int, default=0)
    a = ap.parse_args()
    syms = list(json.load(open(a.pool))["qv"])[a.from_rank : a.from_rank + a.top]
    kinds = a.kinds.split(",")
    msgs = []
    if a.ex == "bitget":
        args = [{"instType": "USDT-FUTURES", "channel": k, "instId": s} for k in kinds for s in syms]
        msgs = [{"op": "subscribe", "args": args[i : i + a.batch]} for i in range(0, len(args), a.batch)]
    elif a.ex == "bitget3":
        args = [{"instType": "usdt-futures", "topic": k, "symbol": s} for k in kinds for s in syms]
        msgs = [{"op": "subscribe", "args": args[i : i + a.batch]} for i in range(0, len(args), a.batch)]
    elif a.ex == "htx":
        for k in kinds:
            for s in syms:
                if k == "funding_rate":
                    msgs.append({"op": "sub", "topic": f"public.{s}.funding_rate", "cid": f"fr-{s}"})
                else:
                    msgs.append({"sub": f"market.{s}.{k}", "id": f"{k}-{s}"})
    elif a.ex == "bingx":
        for k in kinds:
            for s in syms:
                msgs.append({"id": f"{k}-{s}", "reqType": "sub", "dataType": f"{s}@{k}"})
    json.dump(msgs, open(a.out, "w"))
    print(a.out, "symbols", len(syms), "messages", len(msgs))


if __name__ == "__main__":
    main()
