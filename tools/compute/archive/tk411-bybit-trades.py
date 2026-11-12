#!/usr/bin/env python3
"""TK-411 (возврат Судьи): свечи 1 м Bybit linear за 2026-09 из дневных архивов сделок public.bybit.com/trading — для контрактов,
которых /v5/market/kline не отдаёт (закрытые: ASP/ICX/SCRT, недельные -DDSEP26). Минута без сделок в выход не пишется.
Архив: timestamp(с, дробная),symbol,side,size(база),price,...; v = Σ size, quote_v = Σ size·price, o/c — по времени сделки (при равенстве — порядок файла),
o/h/l/c — исходные строки цен. Выход как у Gate/Bybit: <root>/candles/bybit-linear/СИМВОЛ.csv.gz + cov/bybit-linear-СИМВОЛ.tsv
(«linear символ минут первая последняя файлов_200»). Дни без файла (404) — в cov/bybit-trades-missing.tsv.
Использование: tk411-bybit-trades.py <root> <список-символов.txt> [потоков=4] [первый_день=2026-09-01] [последний_день=2026-09-30] [выход=candles/bybit-linear]
Потоков по умолчанию 4: бан 23.10 на public.bybit.com был при 32 (Судья 26.10: ≤4).
"""
import datetime as dt
import gzip
import io
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://public.bybit.com/trading/"
FMT = "%d,%s,%s,%s,%s,%.12g,%.12g,%d\n"


def fetch(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(10 * (attempt + 1) if e.code in (403, 429) else 3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("не вышло: " + url)


def add_day(raw, mins):
    n = 0
    with gzip.open(io.BytesIO(raw), "rt", encoding="ascii", newline="") as f:
        f.readline()
        for line in f:
            p = line.split(",")
            if len(p) < 5:
                continue
            ts = float(p[0])
            px = float(p[4])
            q = float(p[3])
            m = int(ts) // 60
            n += 1
            c = mins.get(m)
            if c is None:
                mins[m] = [ts, p[4], ts, p[4], px, p[4], px, p[4], q, q * px, 1]
                continue
            # c: 0 ts_o, 1 o_s, 2 ts_c, 3 c_s, 4 h_f, 5 h_s, 6 l_f, 7 l_s, 8 v, 9 qv, 10 n
            if ts < c[0]:
                c[0], c[1] = ts, p[4]
            if ts >= c[2]:
                c[2], c[3] = ts, p[4]
            if px > c[4]:
                c[4], c[5] = px, p[4]
            if px < c[6]:
                c[6], c[7] = px, p[4]
            c[8] += q
            c[9] += q * px
            c[10] += 1
    return n


def built(dst):
    """Готовый файл со свечами (не пустая заготовка API с одним заголовком)."""
    if not os.path.exists(dst):
        return False
    with gzip.open(dst, "rt") as f:
        f.readline()
        return bool(f.readline())


def one(sym, days, root, outdir):
    dst = "%s/%s/%s.csv.gz" % (root, outdir, sym)
    if built(dst):
        return sym, None
    mins = {}
    have = 0
    missing = []
    for d in days:
        raw = fetch("%s%s/%s%s.csv.gz" % (BASE, sym, sym, d))
        if raw is None:
            missing.append(d)
            continue
        have += 1
        add_day(raw, mins)
    ks = sorted(mins)
    with gzip.open(dst + ".part", "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
        for m in ks:
            c = mins[m]
            g.write(FMT % (m * 60000, c[1], c[5], c[7], c[3], c[8], c[9], c[10]))
    os.replace(dst + ".part", dst)
    if outdir == "candles/bybit-linear":  # проба сверки (другой выход) охват боевых контрактов не трогает
        with open("%s/cov/bybit-linear-%s.tsv" % (root, sym), "w") as f:
            f.write("linear\t%s\t%d\t%s\t%s\t%d\n" % (sym, len(ks), ks[0] * 60000 if ks else "", ks[-1] * 60000 if ks else "", have))
    return sym, missing


def main():
    root, lst = sys.argv[1], sys.argv[2]
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    d0 = dt.date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else dt.date(2026, 9, 1)
    d1 = dt.date.fromisoformat(sys.argv[5]) if len(sys.argv) > 5 else dt.date(2026, 9, 30)
    outdir = sys.argv[6] if len(sys.argv) > 6 else "candles/bybit-linear"
    os.makedirs("%s/%s" % (root, outdir), exist_ok=True)
    os.makedirs(root + "/cov", exist_ok=True)
    days = [(d0 + dt.timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)]
    syms = [s.strip() for s in open(lst) if s.strip()]
    t0 = time.time()
    with ThreadPoolExecutor(threads) as ex:
        res = list(ex.map(lambda s: one(s, days, root, outdir), syms))
    with open(root + "/cov/bybit-trades-missing.tsv", "w") as f:
        for s, miss in res:
            if miss:
                f.write("%s\t%d\t%s\n" % (s, len(miss), " ".join(miss)))
    if outdir == "candles/bybit-linear":  # список для tk411-finalize.py (причина «собрано из архива сделок»)
        with open(root + "/cov/bybit-trades-list.txt", "w") as f:
            f.write("\n".join(syms) + "\n")
    print("готово: %d символов, %.0f с" % (len(syms), time.time() - t0))


main()
