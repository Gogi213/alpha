"""П-42: общие функции анализа (загрузка суточных рядов, блочный бутстреп, Холм, выбор по оси)."""
import csv
import glob
import math
import os
import sys
from statistics import NormalDist

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".claude", "skills", "alpha-research", "scripts"))
import selection_bias as sb  # noqa: E402

from axes import AXES, BASE, VACUOUS, fmt, vid  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "p42")
SIDES = ("long", "short")
A = slice(0, 30)
B = slice(30, 60)
HALVES = {"A": A, "B": B}
ND = NormalDist()
BLOCK = 4  # ⌈30^{1/3}⌉
NBOOT = 20000

# расширение оси на одно значение, если выбор упёрся в край (None — расширять нельзя: бинарная/ограничена кэшем/край смысла)
EXT = dict(
    natr_min=(0.1, 1.0), t1h=(0.1, 10), t24=(None, 2000), d=(0.2, 1.5), tau=(0.05, 0.8), l_days=(2, 21),
    e=(0.05, 0.8), f=(None, 0.5), r=(3, 120), n_tp=(3, 16), t_low=(0.25, 3), t_high=(2, 12), q=(10, 90), a=(0.5, 4),
    rr=(0.5, 4), s=(1, 12), c=(None, 30), m=(1, 8), mm=(15, 240), s_glob=(2, 8), w=(3, 60), ww=(None, 120),
    p=(None, 8), k=(None, 4), n_bounce=(1, 5),
)


def load_all(roots=None):
    """D[side][vid] = ряд 60 суток; C[side][vid] — путь к каталогу варианта."""
    roots = roots or [d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isdir(d)]
    D = {s: {} for s in SIDES}
    P = {}
    T = {s: {} for s in SIDES}
    for r in sorted(roots):
        for vdir in sorted(glob.glob(os.path.join(r, "*"))):
            if not os.path.isdir(vdir):
                continue
            v = os.path.basename(vdir)
            for s in SIDES:
                f = os.path.join(vdir, s + "-daily.csv")
                if os.path.exists(f):
                    D[s][v] = np.array([float(x["pnl"]) for x in csv.DictReader(open(f))])
                    P[(s, v)] = vdir
                    nA = nB = 0
                    for x in csv.DictReader(open(os.path.join(vdir, s + "-trades.csv"))):
                        if int(x["entry_day"]) < 30:
                            nA += 1
                        else:
                            nB += 1
                    T[s][v] = (nA, nB)
    return D, P, T


def block_boot_means(x, rng, nboot=NBOOT, block=BLOCK):
    n = len(x)
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    idx = idx.reshape(nboot, nb * block)[:, :n]
    return x[idx].mean(axis=1)


def stat(x, rng):
    """Среднее, SE блочного бутстрепа, 95 % перцентильный интервал, двусторонний p по нормали."""
    x = np.asarray(x, dtype=float)
    m = float(x.mean())
    bm = block_boot_means(x, rng)
    se = float(bm.std(ddof=1))
    lo, hi = np.percentile(bm, [2.5, 97.5])
    z = m / se if se > 0 else 0.0
    p = 2 * (1 - ND.cdf(abs(z))) if se > 0 else 1.0
    return dict(mean=m, se=se, lo=float(lo), hi=float(hi), z=z, p=p, n=len(x))


def holm(pvals):
    """Холм по списку p: скорректированные p в исходном порядке."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj


def axis_vals(ax, D_side):
    """Значения оси по порядку: из AXES + найденные расширения."""
    vals = set(AXES[ax])
    lo, hi = EXT.get(ax, (None, None))
    for e in (lo, hi):
        if e is not None and vid(ax, e) in D_side:
            vals.add(e)
    return sorted(vals)


def vid_of(ax, v):
    return "b0" if v == BASE[ax] else vid(ax, v)


def smooth(m):
    n = len(m)
    if n <= 2:
        return list(m)
    return [float(np.mean(m[max(0, i - 1):i + 2])) for i in range(n)]


def pick(sm, base_i, allowed=None):
    """Индекс лучшего сглаженного значения среди допустимых (без сделок в половине — недопустимы); ничья — ближе к базе."""
    idx = [i for i in range(len(sm)) if allowed is None or allowed[i]]
    if not idx:
        return base_i
    best = max(sm[i] for i in idx)
    cands = [i for i in idx if abs(sm[i] - best) < 1e-12]
    return min(cands, key=lambda i: abs(i - base_i))


def drawdown_kpi(x):
    """KPI В-120: дней под водой — самый долгий период до перехая (с хвостом) и p90 по суткам."""
    cum = np.cumsum(x)
    peak = -1e18
    since = 0
    under = []
    longest = 0
    for v in cum:
        if v > peak:
            peak = v
            since = 0
        else:
            since += 1
        under.append(since)
        longest = max(longest, since)
    return dict(longest=int(longest), p90=float(np.percentile(under, 90)), tail=int(under[-1]))


def read_coinday(vdir, side):
    out = {}
    f = os.path.join(vdir, side + "-coinday.csv")
    if not os.path.exists(f):
        return None
    for r in csv.DictReader(open(f)):
        out.setdefault(r["sym"], np.zeros(60))[int(r["day"])] += float(r["pnl"])
    return out


def read_trades(vdir, side):
    f = os.path.join(vdir, side + "-trades.csv")
    return list(csv.DictReader(open(f)))
