#!/usr/bin/env python3
"""Публичные 1м свечи Bybit v5 (linear) за период: dl_klines_1m.py <SYM> <от YYYY-MM-DD> <до YYYY-MM-DD, не включая> <выход.csv>. Формат как study/klines/ref-<SYM>-1m.csv (minute_ms,open,high,low,close,volume,turnover)."""
import csv, json, sys, time, urllib.request, datetime as dt

sym, a, b, out = sys.argv[1:5]
ms = lambda s: int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
lo, hi, rows = ms(a), ms(b) - 60_000, {}
end = hi
while end >= lo:
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&limit=1000&start={lo}&end={end}"
    for attempt in range(5):
        try:
            res = json.load(urllib.request.urlopen(url, timeout=20))
            break
        except Exception:
            time.sleep(2 ** attempt)
    else:
        raise SystemExit(f"{sym}: сбой запроса {url}")
    lst = res["result"]["list"]
    if not lst:
        break
    for r in lst:
        rows[int(r[0])] = r
    end = min(int(r[0]) for r in lst) - 60_000
    time.sleep(0.12)
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["minute_ms", "open", "high", "low", "close", "volume", "turnover"])
    for k in sorted(rows):
        w.writerow([k] + rows[k][1:7])
print(sym, len(rows), "минут", min(rows), max(rows), "ожидалось", (hi - lo) // 60_000 + 1)
