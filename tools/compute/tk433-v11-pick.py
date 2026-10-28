#!/usr/bin/env python3
"""TK-433 v11: 24 событий сентября (seed 433, по одной монете) -> pick.csv; свечи — на calc: `pick` / `cand` режимы.
pick.py pick <s30-каталог> <pick.csv>;  pick.py cand <pick.csv> <корень свечей> <cand.tsv>"""
import csv, glob, gzip, os, random, re, sys

def coin(s):
    s = re.sub(r"(USDT|USDC|PERP|USD)$", "", s)
    return re.sub(r"^(1000000|100000|10000|1000)", "", s)

if sys.argv[1] == "pick":
    pool = {}
    for p in sorted(glob.glob(sys.argv[2] + "/vol-D*.csv")):
        b = os.path.basename(p)[5:-4]
        D, sg = b[:-1], "+" if b[-1] == "p" else "-"
        for r in csv.DictReader(open(p, encoding="utf-8")):
            ex, mk = r["биржа_рынок"].split("-", 1) if "-" in r["биржа_рынок"] else (r["биржа_рынок"], "")
            pool.setdefault(coin(r["инструмент"]), []).append((D, sg, r, ex))
    rng = random.Random(433)
    coins = sorted(pool)
    pick = rng.sample(coins, 24)
    out = csv.writer(open(sys.argv[3], "w", newline="", encoding="utf-8"))
    out.writerow(["n", "id", "биржа", "рынок", "монета", "знак", "t_start_ms", "t_peak_ms", "t_end_ms", "ход_%", "длит_мин", "D", "инструмент_файл"])
    for n, c in enumerate(pick, 1):
        D, sg, r, ex = rng.choice(pool[c])
        exm = r["биржа_рынок"]
        ex, mk = exm.split("-", 1)
        out.writerow([n, "D%s-%s" % (D, r["id"]), ex, mk, r["инструмент"], sg, r["t_start_ms"], r["t_peak_ms"], r["t_peak_ms"], "", r["длит_мин"], D, exm])
else:
    pk = list(csv.DictReader(open(sys.argv[2], encoding="utf-8")))
    out = open(sys.argv[4], "w", encoding="utf-8", newline="")
    out.write("id\tt_ms\to\th\tl\tc\tq\n")
    for r in pk:
        ts, te = int(r["t_start_ms"]), int(r["t_end_ms"])
        pad = max(6 * 3600000, (te - ts) // 4)
        lo, hi = ts - pad, te + pad
        for sub in ("candles-1m", "candles-1m-aug"):
            p = "%s/%s/%s/%s.csv.gz" % (sys.argv[3], sub, r["инструмент_файл"], r["монета"])
            if not os.path.exists(p):
                continue
            for ln in gzip.open(p, "rt"):
                f = ln.split(",")
                if not f[0].isdigit():
                    continue
                t = int(f[0])
                if lo <= t <= hi:
                    out.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (r["id"], t, f[1], f[2], f[3], f[4], f[6]))
