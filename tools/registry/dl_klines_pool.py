#!/usr/bin/env python3
"""1м свечи Bybit по монетам пула за месяцы (дельта к study/klines): dl_klines_pool.py <pool.csv> <выход-каталог> [потоков=4].
Монета-месяц берётся, если в пуле есть сутки in_pool=1 и нет ни study/klines/ref-<SYM>-1m.csv месяца, ни своего файла выхода;
выход <каталог>/<abbr>/ref-<SYM>-1m.csv + manifest.csv (sym,abbr,minutes,expected,status). Возобновляемо (готовый файл пропускается)."""
import calendar, collections, csv, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dl_klines_1m as dl

YEAR = 2026
ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
STUDY = "/data/tk046/{abbr}/study/klines/ref-{sym}-1m.csv"

pool, outdir = sys.argv[1], sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 4
need = collections.defaultdict(set)
for r in csv.DictReader(open(pool, encoding="utf-8")):
    if r["in_pool"] == "1":
        need[int(r["day"][5:7])].add(r["sym"])
jobs = [(m, s) for m in sorted(need) for s in sorted(need[m]) if not os.path.exists(STUDY.format(abbr=ABBR[m - 1], sym=s))]
print("монета-месяцев всего", sum(len(v) for v in need.values()), "скачать", len(jobs), flush=True)


def one(job):
    m, sym = job
    out = os.path.join(outdir, ABBR[m - 1], f"ref-{sym}-1m.csv")
    days = calendar.monthrange(YEAR, m)[1]
    a, b = f"{YEAR}-{m:02d}-01", (f"{YEAR}-{m + 1:02d}-01" if m < 12 else f"{YEAR + 1}-01-01")
    expected = days * 1440
    if os.path.exists(out):
        return sym, ABBR[m - 1], sum(1 for _ in open(out, encoding="utf-8")) - 1, expected, "have"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rows = dl.fetch(sym, a, b)
    dl.write(rows, out + ".tmp"); os.replace(out + ".tmp", out)
    return sym, ABBR[m - 1], len(rows), expected, "ok" if len(rows) == expected else ("empty" if not rows else "partial")


os.makedirs(outdir, exist_ok=True)
with open(os.path.join(outdir, "manifest.csv"), "w", encoding="utf-8", newline="") as f, ThreadPoolExecutor(threads) as ex:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["sym", "abbr", "minutes", "expected", "status"])
    for i, r in enumerate(ex.map(one, jobs), 1):
        w.writerow(r); f.flush()
        if i % 50 == 0:
            print(i, "из", len(jobs), flush=True)
print("готово", flush=True)
