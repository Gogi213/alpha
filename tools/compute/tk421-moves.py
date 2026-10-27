#!/usr/bin/env python3
"""TK-421 шаг A (протокол P-TK421 v3): по каждому инструменту — ходы R_T/D_T (V0: a = ln(1+R), V1: z = a/σ_T) на сетке T,
гистограммы a и z (по половинам месяца) и суточные максимумы (для кластеров монета-день). Пороги K1/K2 — шаг B (tk421-thresh.py).
Использование: tk421-moves.py <корень свечей> <выход-каталог> [процессов] [макс. инструментов на рынок] [месяц ГГГГ-ММ] [art.json]
art.json (tk421-art.py) — минуты-артефакты рынка исключаются из строк (возврат Судьи 27.10)
Ход: R_T = max_{t1<t2, t2−t1≤T мин} high[t2]/low[t1]−1 (памп); дамп симметрично high[t1]/low[t2]−1. Окна по времени
минуты (Gate: минуты без сделок строк не имеют, не заполняются, В-172). σ_T = MAD×1,4826 логдоходностей закрытий блоков T
минут (последнее закрытие блока; пары соседних блоков). MAD = 0 → σ нет, z не считается. Данные как отдаёт биржа."""
import gzip
import json
import math
import os
import sys
from collections import deque
from datetime import datetime, timezone
from multiprocessing import Pool

TS = (5, 15, 60, 240, 1440)
MKT = (("bybit", "linear", "bybit-linear"), ("bybit", "spot", "bybit-spot"),
       ("gate", "futures_usdt", "gate-futures_usdt"), ("gate", "spot", "gate-spot"))
MIN = 60000
DAY = 86400000
NB = 400
A_LO, A_HI = 0.001, 4.0
Z_LO, Z_HI = 0.3, 300.0
EXCL = {}  # {подкаталог рынка: множество минут (мс) — артефакты данных, tk421-art.py; такие строки не читаются}


def load_excl(path):
    for k, v in json.load(open(path))["результат"].items():
        EXCL[k] = set(v.get("артефакт_минут", []))


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:])
    a = datetime(y, m, 1, tzinfo=timezone.utc)
    b = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def load(path, t0, t1):
    t, h, l, c = [], [], [], []
    ex = EXCL.get(os.path.basename(os.path.dirname(path)), ())
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 5)
            ti = int(p[0])
            if ti < t0 or ti >= t1 or ti in ex:
                continue
            hi = float(p[2]); lo = float(p[3]); ci = float(p[4])
            if ci <= 0 or lo <= 0:
                continue
            t.append(ti); h.append(hi); l.append(lo); c.append(ci)
    return t, h, l, c


def moves(t, h, l, T, up):
    """a_T на каждой минуте t2 (ln хода внутри окна T), None если пары нет."""
    w = T * MIN
    dq = deque()
    out = []
    for i, ti in enumerate(t):
        while dq and dq[0][0] < ti - w:
            dq.popleft()
        if dq:
            x = math.log(h[i] / dq[0][1]) if up else math.log(dq[0][1] / l[i])
            out.append(x if x > 0 else 0.0)
        else:
            out.append(0.0)
        v = l[i] if up else h[i]
        if up:
            while dq and dq[-1][1] >= v:
                dq.pop()
        else:
            while dq and dq[-1][1] <= v:
                dq.pop()
        dq.append((ti, v))
    return out


def sigma(t, c, T, t0):
    last = {}
    for ti, ci in zip(t, c):
        last[(ti - t0) // (T * MIN)] = ci
    ks = sorted(last)
    r = [math.log(last[k] / last[k - 1]) for k in ks if k - 1 in last]
    if len(r) < 30:
        return 0.0
    r.sort()
    med = r[len(r) // 2]
    d = sorted(abs(x - med) for x in r)
    return d[len(d) // 2] * 1.4826


def binidx(x, lo, step):
    if x <= 0:
        return 0
    k = int((math.log(x) - lo) / step) + 1
    return 0 if k < 0 else (NB if k > NB else k)


def one(arg):
    ex, mk, d, sym, t0, t1 = arg
    t, h, l, c = load(os.path.join(d, sym + ".csv.gz"), t0, t1)
    if len(t) < 100:
        return {"биржа": ex, "рынок": mk, "инструмент": sym, "минут": len(t), "пропуск": "мало минут"}
    half = t0 + (t1 - t0) // 2
    nd = (t1 - t0) // DAY
    la, sa = math.log(A_LO), (math.log(A_HI) - math.log(A_LO)) / NB
    lz, sz = math.log(Z_LO), (math.log(Z_HI) - math.log(Z_LO)) / NB
    res = {"биржа": ex, "рынок": mk, "инструмент": sym, "минут": len(t), "sigma": {}, "ha": {}, "hz": {}, "dmax_a": {}, "dmax_z": {}}
    for T in TS:
        sg = sigma(t, c, T, t0)
        res["sigma"][T] = sg
        for up, name in ((True, "+"), (False, "-")):
            k = "%d%s" % (T, name)
            a = moves(t, h, l, T, up)
            ha = [[0] * (NB + 1), [0] * (NB + 1)]
            hz = [[0] * (NB + 1), [0] * (NB + 1)]
            da = [0.0] * nd
            dz = [0.0] * nd
            for ti, x in zip(t, a):
                s = 0 if ti < half else 1
                ha[s][binidx(x, la, sa)] += 1
                dd = (ti - t0) // DAY
                if x > da[dd]:
                    da[dd] = x
                if sg > 0:
                    z = x / sg
                    hz[s][binidx(z, lz, sz)] += 1
                    if z > dz[dd]:
                        dz[dd] = z
            res["ha"][k] = ha
            res["hz"][k] = hz if sg > 0 else None
            res["dmax_a"][k] = [round(x, 5) for x in da]
            res["dmax_z"][k] = [round(x, 3) for x in dz] if sg > 0 else None
    return res


def main():
    root, out = sys.argv[1], sys.argv[2]
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    lim = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    ym = sys.argv[5] if len(sys.argv) > 5 else "2026-09"
    if len(sys.argv) > 6:
        load_excl(sys.argv[6])
    t0, t1 = month_bounds(ym)
    os.makedirs(out, exist_ok=True)
    with Pool(procs) as pool:
        for ex, mk, sub in MKT:
            d = os.path.join(root, sub)
            syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
            if lim:
                syms = syms[:lim]
            args = [(ex, mk, d, s, t0, t1) for s in syms]
            n = 0
            with open(os.path.join(out, "moves-%s-%s-%s.jsonl" % (ym, ex, mk)), "w") as fo:
                for r in pool.imap_unordered(one, args, chunksize=4):
                    fo.write(json.dumps(r, ensure_ascii=False) + "\n")
                    n += 1
            print(ex, mk, n, "инструментов", flush=True)


if __name__ == "__main__":
    main()
