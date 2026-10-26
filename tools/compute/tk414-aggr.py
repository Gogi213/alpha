#!/usr/bin/env python3
"""TK-414 возврат п.3: доля агрессивных покупок по ногам (событийные сделки, поток без сохранения сырья).
Вход: legs csv слоя 2, gate-list.tsv (fut<TAB>инстр<TAB>quanto), выход csv. Окна на ногу (UTC, минуты как в свечах TK-411):
 первая половина [старт, середина), вторая [середина, пик+1 мин), после [пик+1 мин, +30 мин). Агрессор = сторона тейкера.
Gate фьюч: ts,id,price,size (size>0 покупка), v=|size|·quanto; Gate спот: ts,id,price,amount,side; Bybit linear/spot: архивы public.bybit.com.
Выход: по ноге buy/sell в $ и штуках по трём окнам. Использование: tk414-aggr.py <legs.csv> <gate-list.tsv> <выход.csv> [процессов=4]"""
import csv, gzip, io, sys, time, urllib.request, urllib.error
from bisect import bisect_left, bisect_right
from calendar import timegm
from collections import defaultdict
from datetime import datetime, timedelta
from multiprocessing import Pool

AFTER = 1800


def ep(s):
    return timegm(datetime.strptime(s, "%Y-%m-%dT%H:%M").timetuple())


def opener(url):
    for a in range(6):
        try:
            return urllib.request.urlopen(url, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(10 * (a + 1) if e.code in (403, 429) else 3 * (a + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3 * (a + 1))
    raise RuntimeError("не вышло: " + url)


def work(arg):
    (ex, mk, ins), legs, quanto = arg
    starts = [l[0] for l in legs]
    mids = [(l[0] + l[1] + 60) // 2 for l in legs]
    ends = [l[1] + 60 for l in legs]
    acc = [[0.0] * 12 for _ in legs]  # окна 0,1,2 × (buy$, sell$, nbuy, nsell)
    t_lo, t_hi = starts[0], ends[-1] + AFTER

    def feed(ts, px, q, buy):
        if ts < t_lo or ts >= t_hi:
            return
        v = px * q
        i = bisect_right(starts, ts) - 1
        if i >= 0 and ts < ends[i]:
            w = 0 if ts < mids[i] else 1
        else:
            i = bisect_right(ends, ts) - 1
            if i < 0 or ts >= ends[i] + AFTER:
                return
            w = 2
        a = acc[i]
        k = w * 4 + (0 if buy else 1)
        a[k] += v
        a[k + 2] += 1

    miss = []
    if ex == "gate":
        u = ("futures_usdt/trades" if mk == "futures_usdt" else "spot/deals")
        r = opener("https://download.gatedata.org/%s/202609/%s-202609.csv.gz" % (u, ins))
        if r is None:
            miss.append("202609")
        else:
            with gzip.open(r, "rt", encoding="ascii", newline="") as f:
                for line in f:
                    p = line.split(",")
                    try:
                        if mk == "futures_usdt":
                            s = float(p[3]); feed(float(p[0]), float(p[2]), abs(s) * quanto, s > 0)
                        else:
                            feed(float(p[0]), float(p[2]), float(p[3]), p[4].strip() == "buy")
                    except (ValueError, IndexError):
                        pass
    else:
        d0 = datetime.utcfromtimestamp(t_lo).date(); d1 = datetime.utcfromtimestamp(t_hi).date()
        d = d0
        while d <= d1:
            ds = d.isoformat()
            if mk == "linear":
                url = "https://public.bybit.com/trading/%s/%s%s.csv.gz" % (ins, ins, ds)
            else:
                url = "https://public.bybit.com/spot/%s/%s_%s.csv.gz" % (ins, ins, ds)
            r = opener(url)
            if r is None:
                miss.append(ds)
            else:
                with gzip.open(io.BytesIO(r.read()), "rt", encoding="ascii", newline="") as f:
                    f.readline()
                    for line in f:
                        p = line.split(",")
                        try:
                            if mk == "linear":  # timestamp(с),symbol,side,size,price
                                feed(float(p[0]), float(p[4]), float(p[3]), p[2] == "Buy")
                            else:  # id,timestamp(мс),price,volume,side
                                feed(float(p[1]) / 1000, float(p[2]), float(p[3]), p[4].strip().lower() == "buy")
                        except (ValueError, IndexError):
                            pass
            d += timedelta(days=1)
    return (ex, mk, ins), legs, acc, miss


def main():
    legs_csv, gl, out = sys.argv[1:4]
    nproc = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    quanto = {}
    for line in open(gl, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if p[0] == "fut":
            quanto[p[1]] = float(p[2])
    by = defaultdict(list)
    for r in csv.DictReader(open(legs_csv, encoding="utf-8")):
        by[(r["биржа"], r["рынок"], r["инструмент"])].append((ep(r["начало_utc"]), ep(r["пик_utc"]), r))
    jobs = []
    for k, ls in by.items():
        ls.sort(key=lambda x: x[0])
        jobs.append((k, [(a, b) for a, b, _ in ls], quanto.get(k[2], 1.0)))
    meta = {k: [r for _, _, r in sorted(v, key=lambda x: x[0])] for k, v in by.items()}
    jobs.sort(key=lambda j: -len(j[1]))
    head = ["биржа", "рынок", "инструмент", "знак", "начало_utc", "пик_utc", "форма"]
    for w in ("h1", "h2", "после"):
        head += [w + "_buy_usd", w + "_sell_usd", w + "_buy_n", w + "_sell_n"]
    with Pool(nproc) as pool, open(out, "w", encoding="utf-8", newline="") as fo, open(out + ".miss.tsv", "w") as fm:
        wr = csv.writer(fo); wr.writerow(head)
        for k, legs, acc, miss in pool.imap_unordered(work, jobs):
            if miss:
                fm.write("%s\t%s\t%s\t%s\n" % (k[0], k[1], k[2], ",".join(miss))); fm.flush()
            for r, a in zip(meta[k], acc):
                wr.writerow([r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"], r["пик_utc"], r["форма"]] +
                            ["%.6g" % x if i % 4 < 2 else "%d" % x for i, x in enumerate([a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11]])])
            fo.flush()


main()
