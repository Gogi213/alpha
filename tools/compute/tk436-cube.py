#!/usr/bin/env python3
"""TK-436 (G-017): куб признаков «инструмент × минута» на месяц — один раз, дальше детекторы читают memmap.
  tk436-cube.py candles <месяц ГГГГ-ММ> <корень свечей> <корень куба> [<доп. корни свечей через запятую|->]
      <куб>/<sub>.npy  float64 (S, 5, T): o,h,l,c,q (q = quote_v); NaN = нет свечи (как в load() tk433-features), <sub>.syms — список.
  tk436-cube.py trades <месяц> <корень /data/tk417> <корень куба> [<процессов>]
      <куб>/trades-<sub>.npy float64 (S, 6, T): buy_q, sell_q, buy_n, sell_n, max и min цены сделки по минуте (0 = сделок нет) (только поминутно, В-172/запрет посекундных не затронуты);
      sub = bybit-linear (foreignNotional, сторона Buy/Sell тикета) и bybit-spot (price*volume, buy/sell); сумма в порядке файла.
  tk436-cube.py oi <месяц> <корень tk415/out> <корень куба>
      <куб>/oi-<sub>.npy float64 (S, K, T5): сетка 5 мин от начала куба (lo, тот же отступ PAD), NaN = нет записи; значения как в файле, без заполнения
      (as-of читает потребитель: tk422 берёт «последняя запись ≤ t»). bybit-linear: open_interest, funding_rate; gate-futures_usdt: open_interest,
      long_liq_usd_new, short_liq_usd_new, funding_rate (ликвидации Gate — потоковые за 5 мин, поминутно не делятся). Записи вне сетки 5 мин — счётчик в <sub>.off.
  tk436-cube.py check <куб> <sub> <инструмент> <корень свечей> — c и q куба == независимый разбор csv.gz (точное равенство).
  tk436-cube.py checkoi <куб> <sub> <инструмент> <корень tk415/out> — oi-куб == независимый разбор read_5m (точное равенство)."""
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


OI_CH = {"bybit-linear": ("bybit", {"oi": ("open_interest",), "funding": ("funding_rate",)}),
         "gate-futures_usdt": ("gate", {"oi": ("open_interest", "long_liq_usd_new", "short_liq_usd_new"), "funding": ("funding_rate",)})}


def oi_channels(sub):
    ex, kinds = OI_CH[sub]
    return ex, [(k, c) for k, cs in kinds.items() for c in cs]


def read_oi_file(fn, col, lo, T5):
    """одна колонка 5-минутного файла -> (массив (T5,) с NaN, число записей вне сетки). Пустая ячейка = NaN (в read_5m это None)."""
    out = np.full(T5, np.nan)
    off = 0
    if not os.path.exists(fn):
        return out, off
    with gzip.open(fn, "rt") as f:
        h = next(f).rstrip("\n").split(",")
        if col not in h:
            return out, off
        ci = h.index(col)
        for line in f:
            p = line.rstrip("\n").split(",")
            m, r = divmod(int(p[0]), MIN)
            k, r5 = divmod(m - lo, 5)
            if r or r5:
                off += 1
            elif 0 <= k < T5:
                out[k] = float(p[ci]) if ci < len(p) and p[ci] != "" else np.nan
    return out, off


def _oi_task(a):
    oroot, ex, sym, chans, lo, T5 = a
    res = np.full((len(chans), T5), np.nan)
    off = 0
    for j, (kind, col) in enumerate(chans):
        res[j], o = read_oi_file("%s/%s/%s/%s.csv.gz" % (oroot, ex, kind, sym), col, lo, T5)
        off += o
    return res, off


def cmd_oi(av):
    ym, oroot, outd = av[:3]
    lo, hi = month_bounds(ym)
    lo -= PAD * 1440
    assert lo % 5 == 0 and (hi - lo) % 5 == 0, "сетка 5 мин должна совпадать с границами куба"
    T5 = (hi - lo) // 5
    pool = Pool(int(os.environ.get("NPROC", "15")))
    for sub in OI_CH:
        ex, chans = oi_channels(sub)
        syms = sorted(fn[:-7] for fn in os.listdir("%s/%s/oi" % (oroot, ex)) if fn.endswith(".csv.gz"))
        mm = np.lib.format.open_memmap("%s/oi-%s.npy" % (outd, sub), mode="w+", dtype=np.float64, shape=(len(syms), len(chans), T5))
        offs = 0
        for i, (arr, o) in enumerate(pool.imap(_oi_task, [(oroot, ex, s, chans, lo, T5) for s in syms], chunksize=4)):
            mm[i] = arr
            offs += o
        mm.flush()
        open("%s/oi-%s.syms" % (outd, sub), "w").write("\n".join(syms) + "\n")
        open("%s/oi-%s.ch" % (outd, sub), "w").write("\n".join("%s/%s" % c for c in chans) + "\n")
        open("%s/oi-%s.lo" % (outd, sub), "w").write("%d\n" % lo)
        open("%s/oi-%s.off" % (outd, sub), "w").write("%d\n" % offs)
        print("oi", sub, len(syms), "инструментов,", len(chans), "каналов, вне сетки 5 мин:", offs, flush=True)


def cmd_checkoi(av):
    """независимо от кода куба: разбор как read_5m из tk422-features (список (t_ms, {колонка: float|None}))."""
    cube, sub, sym, oroot = av[:4]
    syms = open("%s/oi-%s.syms" % (cube, sub)).read().split()
    chs = open("%s/oi-%s.ch" % (cube, sub)).read().split()
    lo = int(open("%s/oi-%s.lo" % (cube, sub)).read())
    mm = np.load("%s/oi-%s.npy" % (cube, sub), mmap_mode="r")
    got = np.array(mm[syms.index(sym)])
    ex = OI_CH[sub][0]
    bad = n = 0
    seen = np.zeros(got.shape, bool)
    for j, ch in enumerate(chs):
        kind, col = ch.split("/")
        fn = "%s/%s/%s/%s.csv.gz" % (oroot, ex, kind, sym)
        with gzip.open(fn, "rt") as f:
            h = next(f).rstrip("\n").split(",")
            ix = h.index(col) if col in h else None
            for line in f:
                p = line.rstrip("\n").split(",")
                t = int(p[0])
                if ix is None or t % 300000 or (t // MIN - lo) < 0 or (t // MIN - lo) // 5 >= got.shape[1]:
                    continue
                k = (t // MIN - lo) // 5
                v = float(p[ix]) if ix < len(p) and p[ix] != "" else None
                seen[j, k] = True
                n += 1
                if v is None:
                    bad += not np.isnan(got[j, k])
                else:
                    bad += not (got[j, k] == v)
    bad += int((np.isfinite(got) & ~seen).sum())      # в кубе нет ничего, чего нет в файлах
    print("checkoi", sub, sym, "OK" if bad == 0 else "РАСХОЖДЕНИЙ %d" % bad, "значений", n)
    sys.exit(0 if bad == 0 else 1)


AWK_LIN = r"""NR>1{m=int($1/60); k=m" "($3=="Buy"); s[k]+=$10; n[k]++} END{for(k in s) printf "%s %.17g %d\n", k, s[k], n[k]}"""
AWK_SPOT = r"""NR>1{m=int($2/60000); k=m" "($5=="buy"); s[k]+=$3*$4; n[k]++} END{for(k in s) printf "%s %.17g %d\n", k, s[k], n[k]}"""


def _trade_task(a):
    sub, path, lo, T = a
    awk = AWK_LIN if sub == "bybit-linear" else AWK_SPOT
    r = subprocess.run("zcat %s | mawk -F, '%s'" % (path, awk), shell=True, capture_output=True, text=True, check=True)
    out = np.zeros((6, T))
    for ln in r.stdout.splitlines():
        m, buy, s, n, h, l = ln.split()
        i = int(m) - lo
        if 0 <= i < T:
            j = 0 if buy == "1" else 1
            out[j, i] += float(s)
            out[2 + j, i] += int(n)
            h, l = float(h), float(l)
            if out[4, i] == 0 or h > out[4, i]:
                out[4, i] = h
            if out[5, i] == 0 or l < out[5, i]:
                out[5, i] = l
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
        mm = np.lib.format.open_memmap("%s/trades-%s.npy" % (outd, sub), mode="w+", dtype=np.float64, shape=(len(syms), 6, T))
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
    {"candles": cmd_candles, "trades": cmd_trades, "check": cmd_check, "oi": cmd_oi, "checkoi": cmd_checkoi}[sys.argv[1]](sys.argv[2:])
