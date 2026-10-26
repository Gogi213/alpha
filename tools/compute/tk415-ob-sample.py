#!/usr/bin/env python3
"""TK-415: выборочный замер размеров архивов стакана за 2026-09 по HEAD (без скачивания), оценка объёма на весь месяц.
  gate futures_usdt : download.gatedata.org/futures_usdt/orderbooks/202609/СИМ-YYYYMMDDHH.csv.gz (почасовые), 3 суток × 24 ч
  gate spot         : download.gatedata.org/spot/orderbooks/202609/СИМ-YYYYMMDDHH.csv.gz (проба существования)
  bybit linear/spot : quote-saver.bycsi.com/orderbook/{linear,spot}/СИМ/YYYY-MM-DD_СИМ_ob200.data.zip (суточные), 30 суток
Выборка: N случайных инструментов (seed 11) из охвата свечей TK-411, + BTC. Потоков 4.
Выход <выход.tsv> «источник символ ключ байт|404». Использование: tk415-ob-sample.py <охват csv> <выход.tsv> [N=24]
"""
import csv
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

cov, out = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 24
rows = list(csv.DictReader(open(cov, encoding="utf-8")))
rows = [r for r in rows if int(r["минут_с_данными"]) > 0]
random.seed(11)
items = []
for ex, mk, key in (("gate", "futures_usdt", "gate-fut"), ("gate", "spot", "gate-spot"), ("bybit", "linear", "bybit-linear"), ("bybit", "spot", "bybit-spot")):
    pool = [r["инструмент"] for r in rows if r["биржа"] == ex and r["рынок"] == mk]
    base = "BTC_USDT" if ex == "gate" else "BTCUSDT"
    smp = [base] + random.sample([s for s in pool if s != base], min(N, len(pool) - 1))
    for s in smp:
        if ex == "gate":
            for d in (1, 15, 29):
                for h in range(24):
                    t = "202609%02d%02d" % (d, h)
                    pre = "futures_usdt" if mk == "futures_usdt" else "spot"
                    items.append((key, s, t, "https://download.gatedata.org/%s/orderbooks/202609/%s-%s.csv.gz" % (pre, s, t)))
        else:
            for d in range(1, 31):
                day = "2026-09-%02d" % d
                items.append((key, s, day, "https://quote-saver.bycsi.com/orderbook/%s/%s/%s_%s_ob200.data.zip" % (mk, s, day, s)))


def head(it):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(it[3], method="HEAD"), timeout=30) as r:
                return it[:3] + (r.headers.get("Content-Length", "?"),)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return it[:3] + (str(e.code),)
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return it[:3] + ("err",)


with ThreadPoolExecutor(4) as pool, open(out, "w", encoding="utf-8") as g:
    for i, r in enumerate(pool.map(head, items)):
        g.write("\t".join(r) + "\n")
        if i % 500 == 0:
            g.flush()
            print(i, len(items), flush=True)
