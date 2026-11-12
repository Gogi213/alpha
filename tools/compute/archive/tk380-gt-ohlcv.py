#!/usr/bin/env python3
"""TK-380: минутные свечи пулов GeckoTerminal (бесплатно, без ключа) за 15.10 00:00 — 22.10 24:00 UTC.
Вход: tk380-pools-*.csv (coin,chain,pool,...,vol24). Выход: <out>/<chain>/<pool>.csv (ts,o,h,l,c,vol_usd), <out>/ALLDONE.json.
Пулы по убыванию vol24; готовые файлы пропускаются; лимит ~30 запросов/мин."""
import csv, json, os, sys, time, urllib.request, urllib.error
pools_csv, out = sys.argv[1], sys.argv[2]
T0, T1 = 1792022400, 1792713600  # 15.10 00:00 .. 23.10 00:00 UTC
NET = {"ethereum": "eth", "bsc": "bsc", "solana": "solana", "base": "base", "arbitrum": "arbitrum",
       "sui": "sui-network", "polygon": "polygon_pos", "optimism": "optimism", "tron": "tron", "avalanche": "avax"}
rows = list(csv.DictReader(open(pools_csv, encoding="utf-8")))
rows.sort(key=lambda r: -float(r["vol24"] or 0))
GAP = 2.2
last = [0.0]
def get(url):
    for a in range(6):
        w = GAP - (time.time() - last[0])
        if w > 0: time.sleep(w)
        last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "curl/8"})
            return 200, json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20 * (a + 1)); continue
            if e.code in (404, 400): return e.code, None
            time.sleep(5)
        except Exception:
            time.sleep(5)
    return -1, None
stat = {"ok": 0, "empty": 0, "err": 0, "calls": 0, "candles": 0}
for i, r in enumerate(rows):
    net = NET.get(r["chain"]); d = os.path.join(out, r["chain"]); os.makedirs(d, exist_ok=True)
    f = os.path.join(d, r["pool"] + ".csv")
    if os.path.exists(f): continue
    if not net:
        stat["err"] += 1; continue
    bt, got, code = T1, {}, 200
    while bt > T0:
        code, j = get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{r['pool']}/ohlcv/minute?aggregate=1&limit=1000&before_timestamp={bt}")
        stat["calls"] += 1
        if j is None: break
        lst = j["data"]["attributes"]["ohlcv_list"]
        if not lst: break
        for c in lst:
            if T0 <= c[0] < T1: got[c[0]] = c
        m = min(c[0] for c in lst)
        if m >= bt: break
        bt = m
    if code in (200,) or got:
        with open(f + ".tmp", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["ts", "o", "h", "l", "c", "vol_usd"])
            for ts in sorted(got): w.writerow(got[ts])
        os.replace(f + ".tmp", f)
        stat["ok" if got else "empty"] += 1; stat["candles"] += len(got)
    else:
        stat["err"] += 1
    if i % 20 == 0: print(time.strftime("%H:%M:%S"), i, len(rows), stat, flush=True)
json.dump({"done": True, **stat}, open(os.path.join(out, "ALLDONE.json"), "w"))
print("DONE", stat)
