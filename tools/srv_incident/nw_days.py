#!/usr/bin/env python3
"""TK-581: общие эпизоды `netwatch` по дням — тот же критерий, что `nw_onset.py` (секунда «плохая», если >= 2 из 4 TCP-целей: таймаут / нет замера / RTT >= 1000,
Binance >= 1500; эпизод = плохие секунды с разрывом <= 20 с), но секунды, где netwatch ничего не писал (процесс не работал), не считаются плохими: они «не покрыты»,
дыры покрытия >= 60 с выводятся отдельно. По каждому дню: покрытие, число эпизодов с >= 3 / >= 10 / >= 15 плохих секунд, список эпизодов >= 10 плохих секунд,
по часам — число плохих секунд в эпизодах >= 10. python3 nw_days.py <день> [<день> ...]"""
import json, sys, collections, datetime as dt
TCP = ["binance", "bybit", "okx", "bingx"]
fmt = lambda s: dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%H:%M:%S")
for day in sys.argv[1:]:
    d0 = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    sec = collections.defaultdict(dict)
    for line in open(f"/data/netwatch/{day}.jsonl"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if "ts" in o:
            sec[int(o["ts"])][o["n"]] = o["rtt"]
    covered = sorted(sec)
    gaps = [(a, b) for a, b in zip(covered, covered[1:]) if b - a >= 60]
    bad = []
    for s in covered:
        nb = 0
        for n in TCP:
            v = sec[s].get(n, "missing")
            if v == "missing" or v is None or v >= (1500 if n == "binance" else 1000):
                nb += 1
        if nb >= 2:
            bad.append(s)
    eps = []
    for s in bad:
        if eps and s - eps[-1][-1] <= 20:
            eps[-1].append(s)
        else:
            eps.append([s])
    cnt = {th: sum(1 for e in eps if len(e) >= th) for th in (3, 10, 15)}
    print(f"=== {day}: покрыто {len(covered)} с ({covered[0] - d0}…{covered[-1] - d0}), {fmt(covered[0])}–{fmt(covered[-1])}; дыр покрытия >= 60 с: {len(gaps)}"
          + "".join(f" [{fmt(a)}–{fmt(b)}]" for a, b in gaps[:6]))
    print(f"  эпизодов (>= 3 / >= 10 / >= 15 плохих секунд): {cnt[3]} / {cnt[10]} / {cnt[15]}")
    for e in eps:
        if len(e) >= 10:
            print(f"  {fmt(e[0])}–{fmt(e[-1])} ({e[-1] - e[0] + 1} с, плохих {len(e)})")
    hr = collections.Counter()
    for e in eps:
        if len(e) >= 10:
            for s in e:
                hr[(s - d0) // 3600] += 1
    print("  плохих секунд в эпизодах >= 10, по часам UTC:", " ".join(f"{h:02d}:{hr[h]}" for h in sorted(hr)))
