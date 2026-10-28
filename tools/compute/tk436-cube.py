#!/usr/bin/env python3
"""TK-436 (G-017): куб признаков «инструмент × минута» на месяц — один раз, дальше детекторы читают memmap.
  tk436-cube.py candles <месяц ГГГГ-ММ> <корень свечей> <корень куба> [<доп. корни свечей через запятую|->]
      <куб>/<sub>.npy  float64 (S, 5, T): o,h,l,c,q (q = quote_v); NaN = нет свечи (как в load() tk433-features), <sub>.syms — список.
  tk436-cube.py trades <месяц> <корень /data/tk417> <корень куба> [<процессов>]
      <куб>/trades-<sub>.npy float64 (S, 4, T): buy_q, sell_q, buy_n, sell_n по минуте (только поминутно, В-172/запрет посекундных не затронуты);
      sub = bybit-linear (foreignNotional, сторона Buy/Sell тикета) и bybit-spot (price*volume, buy/sell); сумма в порядке файла.
  tk436-cube.py check <куб> <sub> <инструмент> <корень свечей> — c и q куба == независимый разбор csv.gz (точное равенство)."""
import datetime, gzip, os, subprocess, sys
from multiprocessing import Pool
import numpy as np

MIN = 60000
SUBS = ("bybit-linear", "bybit-spot", "gate-futures_usdt", "gate-spot")
F = ("o", "h", "l", "c", "q")
PAD = int(os.environ.get("CUBE_PAD", "15"))   # суток запаса назад в кубе свечей; контроли ±30 сут (tk435-vol) — для точного повтора нужно ≥ 31


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:])
    a = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
    b = datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(a.timestamp() * 1000) // MIN, int(b.timestamp() * 1000) // MIN


def read_candles(fn, lo, T):
    """свечи csv.gz -> (5,T); как tk433-features.load: c>0, минута = t_open_ms//60000."""
    out = np.full((5, T), np.nan)
    with gzip.open(fn, "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 7)
            c = float(p[4])
            if c > 0:
                i = int(p[0]) // MIN - lo
                if 0 <= i < T:
                    out[:, i] = (float(p[1]), float(p[2]), float(p[3]), c, float(p[6] or 0))
    return out


def _cand_task(a):
    """несколько корней: читаются по порядку, поздний перекрывает ранний по минуте (как load() tk433 с roots=(aug, root))."""
    fns, lo, T = a
    out = read_candles(fns[0], lo, T)
    for fn in fns[1:]:
        x = read_candles(fn, lo, T)
        ok = np.isfinite(x[3])
        out[:, ok] = x[:, ok]
    return out


def cmd_candles(av):
    ym, root, outd = av[:3]
    aug = av[3].split(",") if len(av) > 3 and av[3] != "-" else []   # доп. корни через запятую: ранние раньше, основной корень поверх
    lo, hi = month_bounds(ym)
    lo -= PAD * 1440          # запас назад: норма 7 сут, natr суточный (15 суток) у первых окон месяца берут историю прошлого месяца
    T = hi - lo
    os.makedirs(outd, exist_ok=True)
    pool = Pool(int(os.environ.get("NPROC", "15")))
    for sub in SUBS:
        # август лежит в отдельном корне (как у tk433: сначала aug, затем основной корень поверх)
        srcs = {}
        for r in aug + [root]:
            d = "%s/%s" % (r, sub)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith(".csv.gz"):
                        srcs.setdefault(fn[:-7], []).append(d + "/" + fn)
        syms = sorted(srcs)
        if not syms:
            continue
        mm = np.lib.format.open_memmap("%s/%s.npy" % (outd, sub), mode="w+", dtype=np.float64, shape=(len(syms), 5, T))
        for i, arr in enumerate(pool.imap(_cand_task, [(srcs[s], lo, T) for s in syms], chunksize=4)):
            mm[i] = arr
        mm.flush()
        open("%s/%s.syms" % (outd, sub), "w").write("\n".join(syms) + "\n")
        open("%s/%s.lo" % (outd, sub), "w").write("%d\n" % lo)     # первая минута эпохи (колонка 0 оси T)
        print(sub, len(syms), "инструментов", flush=True)


AWK_LIN = r"""NR>1{m=int($1/60); k=m" "($3=="Buy"); s[k]+=$10; n[k]++} END{for(k in s) print k, s[k], n[k]}"""
AWK_SPOT = r"""NR>1{m=int($2/60000); k=m" "($5=="buy"); s[k]+=$3*$4; n[k]++} END{for(k in s) print k, s[k], n[k]}"""


def _trade_task(a):
    sub, path, lo, T = a
    awk = AWK_LIN if sub == "bybit-linear" else AWK_SPOT
    r = subprocess.run("zcat %s | mawk -F, '%s'" % (path, awk), shell=True, capture_output=True, text=True, check=True)
    out = np.zeros((4, T))
    for ln in r.stdout.splitlines():
        m, buy, s, n = ln.split()
        i = int(m) - lo
        if 0 <= i < T:
            j = 0 if buy == "1" else 1
            out[j, i] += float(s)
            out[2 + j, i] += int(n)
    return out


def cmd_trades(av):
    ym, root, outd = av[:3]
    nproc = int(av[3]) if len(av) > 3 else 15
    lo, hi = month_bounds(ym)
    T = hi - lo
    os.makedirs(outd, exist_ok=True)
    pool = Pool(nproc)
    for sub in ("bybit-linear", "bybit-spot"):
        d = "%s/%s" % (root, sub)
        syms = sorted(os.listdir(d)) if os.path.isdir(d) else []
        tasks, owner = [], []
        for si, s in enumerate(syms):
            for fn in sorted(os.listdir("%s/%s" % (d, s))):
                tasks.append((sub, "%s/%s/%s" % (d, s, fn), lo, T))
                owner.append(si)
        mm = np.lib.format.open_memmap("%s/trades-%s.npy" % (outd, sub), mode="w+", dtype=np.float64, shape=(len(syms), 4, T))
        mm[:] = 0
        for si, arr in zip(owner, pool.imap(_trade_task, tasks, chunksize=1)):
            mm[si] += arr          # суточные файлы не пересекаются по минутам
        mm.flush()
        open("%s/trades-%s.syms" % (outd, sub), "w").write("\n".join(syms) + "\n")
        open("%s/trades-%s.lo" % (outd, sub), "w").write("%d\n" % lo)
        print(sub, len(syms), "инструментов", len(tasks), "файлов", flush=True)


def cmd_check(av):
    cube, sub, sym, root = av[:4]
    syms = open("%s/%s.syms" % (cube, sub)).read().split()
    mm = np.load("%s/%s.npy" % (cube, sub), mmap_mode="r")
    T = mm.shape[2]
    lo = int(open("%s/%s.lo" % (cube, sub)).read())
    got = np.array(mm[syms.index(sym)])
    d = {}                   # независимый разбор — копия load() из tk433-features.py: {минута: (close, quote_v)}; root = корни через запятую, поздний поверх раннего
    for r in root.split(","):
        fn = "%s/%s/%s.csv.gz" % (r, sub, sym)
        if not os.path.exists(fn):
            continue
        with gzip.open(fn, "rt") as f:
            f.readline()
            for line in f:
                p = line.split(",", 7)
                c = float(p[4])
                if c > 0:
                    d[int(p[0]) // MIN] = (c, float(p[6] or 0))
    bad = 0
    for i in range(T):
        e = d.get(lo + i)
        if e is None:
            bad += not np.isnan(got[3, i])
        else:
            bad += not (got[3, i] == e[0] and got[4, i] == e[1])
    print("check", sub, sym, "OK" if bad == 0 else "РАСХОЖДЕНИЙ %d" % bad, "ячеек", int(np.isfinite(got[3]).sum()))
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    {"candles": cmd_candles, "trades": cmd_trades, "check": cmd_check}[sys.argv[1]](sys.argv[2:])
