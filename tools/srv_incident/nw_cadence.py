#!/usr/bin/env python3
"""TK-581: нет ли периодичности у отказов TCP-connect netwatch. Секунда «плохая» для цели: таймаут (rtt null) или RTT >= 1000 мс (Binance >= 1500: фон 240 мс; SYN-повтор = ~1 с).
Печатает по дням: число плохих секунд по четырём целям в сумме, по минуте % 10 и по минуте % 5; а для 16.11 — по минутам с 20:30 строки с >= 8 плохих секунд суммарно.
python3 nw_cadence.py <день> [<день> ...]"""
import json, sys, collections, datetime as dt
TCP = {"binance", "bybit", "okx", "bingx"}
for day in sys.argv[1:]:
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    bad = collections.Counter(); tot = collections.Counter(); bad_by_min = collections.Counter()
    for line in open(f"/data/netwatch/{day}.jsonl"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if "ts" not in o or o["n"] not in TCP:
            continue
        m = int((o["ts"] - d0) // 60)
        r = o["rtt"]
        is_bad = r is None or r >= (1500 if o["n"] == "binance" else 1000)
        tot[m] += 1
        if is_bad:
            bad[m] += 1
    mod10 = collections.Counter(); mod5 = collections.Counter(); n10 = collections.Counter()
    for m in tot:
        mod10[m % 10] += bad[m]; mod5[m % 5] += bad[m]; n10[m % 10] += tot[m]
    print(f"=== {day}: плохих секунд всего {sum(bad.values())} из {sum(tot.values())} ({100*sum(bad.values())/max(1,sum(tot.values())):.2f} %)")
    print("  по минуте %10:", {k: f"{mod10[k]} ({100*mod10[k]/max(1,n10[k]):.2f}%)" for k in range(10)})
    top = sorted(bad.items(), key=lambda kv: -kv[1])[:14]
    print("  топ минут:", ", ".join(f"{dt.datetime.fromtimestamp(d0+m*60,dt.timezone.utc).strftime('%H:%M')}={n}" for m, n in sorted(top)))
