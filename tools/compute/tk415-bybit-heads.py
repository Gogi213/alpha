#!/usr/bin/env python3
"""TK-415: размеры архива сделок Bybit за 2026-09 по HEAD (без скачивания): linear — дневные public.bybit.com/trading/СИМ/СИМ<день>.csv.gz,
spot — месячный public.bybit.com/spot/СИМ/СИМ-2026-09.csv.gz. Список — охват свечей TK-411. Потоков ≤4 (бан 23.10 при 32).
Выход: <выход.tsv> «рынок символ день байт|404». Использование: tk415-bybit-heads.py <охват csv> <выход.tsv> [потоков=4]
"""
import csv
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

cov, out = sys.argv[1], sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 4
items = []
with open(cov, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["биржа"] != "bybit" or int(r["минут_с_данными"]) <= 0:
            continue
        if r["рынок"] == "linear":
            for d in range(1, 31):
                day = "2026-09-%02d" % d
                items.append(("linear", r["инструмент"], day, "https://public.bybit.com/trading/%s/%s%s.csv.gz" % (r["инструмент"], r["инструмент"], day)))
        elif r["рынок"] == "spot":
            items.append(("spot", r["инструмент"], "2026-09", "https://public.bybit.com/spot/%s/%s-2026-09.csv.gz" % (r["инструмент"], r["инструмент"])))


def head(it):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(it[3], method="HEAD"), timeout=30) as r:
                return it[:3] + (r.headers.get("Content-Length", "?"),)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return it[:3] + (str(e.code),)
        except Exception:
            pass
        import time
        time.sleep(2 * (attempt + 1))
    return it[:3] + ("err",)


with ThreadPoolExecutor(threads) as pool, open(out, "w", encoding="utf-8") as g:
    for i, r in enumerate(pool.map(head, items)):
        g.write("\t".join(r) + "\n")
        if i % 2000 == 0:
            g.flush()
            print(i, len(items), flush=True)
