#!/usr/bin/env python3
"""TK-037: дневной оборот (turnover) всех USDT-бессрочных Bybit за 01.01–02.10 → CSV.
Список символов — каталог public.bybit.com/trading/ (включает снятые с торгов), свечи — /v5/market/kline D."""
import csv, re, sys, time, json, urllib.request, datetime as dt
OUT = sys.argv[1]; D0 = sys.argv[2]; D1 = sys.argv[3]
def get(u):
    for i in range(5):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha"}), timeout=30).read()
        except Exception as e:
            time.sleep(1 + i)
    raise RuntimeError(u)
syms = sorted(set(re.findall(r'href="([A-Z0-9]+USDT)/"', get("https://public.bybit.com/trading/").decode())))
ms = lambda d: int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
s, e = ms(D0), ms(D1)
n = 0
with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["symbol", "day", "turnover_usdt", "volume"])
    for sym in syms:
        j = json.loads(get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=D&start={s}&end={e}&limit=1000"))
        if j["retCode"] != 0:
            print("skip", sym, j["retCode"], j["retMsg"], file=sys.stderr); continue
        for r in j["result"]["list"]:
            d = dt.datetime.fromtimestamp(int(r[0]) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            w.writerow([sym, d, r[6], r[5]]); n += 1
        time.sleep(0.05)
print(len(syms), "symbols", n, "rows")
