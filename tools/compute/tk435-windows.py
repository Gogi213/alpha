#!/usr/bin/env python3
"""TK-435 (1): локальные тренды окном — ход ≥ X % за ≤ D минут (черновик кода под протокол v11, параметры — по умолчанию).
Использование:
  tk435-windows.py events <корень свечей|-> <l4v8.json|-> <art.json|-> <выход-каталог> [--procs 15] [--ym 2026-09] [--cube <корень куба>]
        [--t0 ГГГГ-ММ-ДД --t1 ГГГГ-ММ-ДД] [--x 5,10,20,30 | q99] [--xq xq.json] [--d 90,180,360,720,1440] [--limit N]
  tk435-windows.py quant <hist.json> <xq.json>      квантили q98/q99/q99,5 |хода| по D и знаку из гистограммы (X протокола v11)
Окно монеты: close 1 м; для конца j старт i ∈ [j−D, j−90], т. е. длительность j−i ∈ [90 мин; D] по построению (быстрее 90 мин — не
событие); ход вверх = close[j] / min(close[j−D..j−90]) − 1 (low раньше high), вниз = 1 − close[j] / max(close[j−D..j−90]) (= 1 − min/max); меньше 0 → 0.
Событие на (X, D, знак): жадно по ходу (убывание), непересекающиеся интервалы [старт, конец] внутри инструмента; один интервал —
одно событие (склейка смежных окон одного хода). E1 имя с плечом; E2 минуты артефактов (art.json) не читаются; E3 пересечение
с рыночным периодом L4 v8 своего знака; E4 фитиль: ≥ 50 % хода возвращается за ≤ 15 мин после пика (порог TK-421). Пара (как
tk433-legs (а)): событие вверх и вниз того же инструмента и (X, D), пик первого = старт второго → «памп→дамп:памп/дамп», иначе
«одиночная». Кластер по монете×знак×(X,D) через рынки/биржи, представитель — наибольший оборот окна. Гистограмма |хода| всех
окон (без плечевых имён) — hist-<ym>.json (по ней `quant`). Ребалансы TK-431 здесь не размечены."""
import argparse
import csv
import importlib.util
import json
import math
import os
import re
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("ev", os.path.join(HERE, "tk421-events.py"))
ev = importlib.util.module_from_spec(sp)
sp.loader.exec_module(ev)
mv, MIN = ev.mv, ev.MIN
LEV = re.compile(r"(\d+[LS]|BULL|BEAR|UP|DOWN)(_?USDT?|_?USD)?$")
MIN_DUR = 90                          # мин: тренд ≥ 1,5 ч (число владельца, В-57); старт окна не ближе MIN_DUR к концу
WICK_MIN, WICK_FRAC = 15, 0.5         # TK-421: ход возвращается ≥ 50 % за ≤ 15 мин после пика
EDGES = np.concatenate([np.linspace(0, 1, 2001)[:-1], np.linspace(1, 50, 491)])
PER = {}
CFG = {}
CUBE, _MM = None, {}


def coin(sym):
    s = sym.split("-")[0]
    for suf in ("_USDT", "USDT", "_USD", "USD"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def day_ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def roll(x, L, f, ident):
    """f-свёртка (min/max) окна [e−L+1, e] за O(n): ван Херк — префиксы/суффиксы по блокам длины L; значения те же, что у swv(...).min()."""
    n = len(x)
    k = -(-(n + L - 1) // L)
    p = np.full(k * L, ident)
    p[L - 1:L - 1 + n] = x
    b = p.reshape(k, L)
    g = f.accumulate(b, axis=1).ravel()
    h = f.accumulate(b[:, ::-1], axis=1)[:, ::-1].ravel()
    return f(h[:n], g[L - 1:L - 1 + n])


def moves(cc, W):
    """(up, dn, lo, hi): ход вверх c[j]/min−1 и вниз 1−c[j]/max по концу j, старт ∈ [j−W, j−MIN_DUR] (длительность ≥ MIN_DUR по
    построению); отрицательный ход → 0; NaN, где закрытия нет или в окне старта нет ни одной свечи."""
    n = len(cc)
    L = W - MIN_DUR + 1                       # ширина окна старта [j−W, j−MIN_DUR]
    lo = np.where(np.isnan(cc), np.inf, cc)
    hi = np.where(np.isnan(cc), -np.inf, cc)
    rmn = roll(lo, L, np.minimum, np.inf)       # rmn[e] = min close[e−L+1..e]
    rmx = roll(hi, L, np.maximum, -np.inf)
    mn = np.concatenate([np.full(MIN_DUR, np.inf), rmn[:n - MIN_DUR]])[:n]
    mx = np.concatenate([np.full(MIN_DUR, -np.inf), rmx[:n - MIN_DUR]])[:n]
    bad = np.isnan(cc) | ~np.isfinite(mn)
    with np.errstate(invalid="ignore"):
        up = np.where(bad, np.nan, np.maximum(cc / mn - 1, 0))
        dn = np.where(bad, np.nan, np.maximum(1 - cc / mx, 0))
    return up, dn, lo, hi


def starts(src, js, W, sgn):
    """индекс старта (argmin/argmax окна [j−W, j−MIN_DUR]) для концов js; src — lo (для вверх) или hi (для вниз) без паддинга."""
    L = W - MIN_DUR + 1
    pad = np.full(L - 1, np.inf if sgn > 0 else -np.inf)
    v = swv(np.concatenate([pad, src]), L)
    out = np.empty(len(js), dtype=np.int64)
    for a in range(0, len(js), 2000):
        blk = js[a:a + 2000]
        w = v[blk - MIN_DUR]
        out[a:a + 2000] = blk - W + (w.argmin(axis=1) if sgn > 0 else w.argmax(axis=1))
    return out


def load_cube(sub, sym, t0, t1):
    """(cc, qq, число свечей) окна [t0, t1) из куба tk436-cube.py (memmap) — то же, что ev.load_q + раскладка по минутам:
    свеча годна при h, l, c > 0 (как load_q), минуты EXCL (артефакты) выкидываются."""
    if sub not in _MM:
        syms = open("%s/%s.syms" % (CUBE, sub)).read().split()
        _MM[sub] = (np.load("%s/%s.npy" % (CUBE, sub), mmap_mode="r"), {s: i for i, s in enumerate(syms)},
                    int(open("%s/%s.lo" % (CUBE, sub)).read()))
    mm, ix, lo = _MM[sub]
    base, n = t0 // MIN, (t1 - t0) // MIN
    k = ix.get(sym)
    if k is None:
        return None
    assert base >= lo and base - lo + n <= mm.shape[2], "окно вне куба"
    x = np.array(mm[k, :, base - lo:base - lo + n])
    with np.errstate(invalid="ignore"):
        ok = (x[1] > 0) & (x[2] > 0) & (x[3] > 0)
    for ti in mv.EXCL.get(sub, ()):
        if t0 <= ti < t1:
            ok[ti // MIN - base] = False
    return np.where(ok, x[3], np.nan), np.where(ok, x[4], 0.0), int(ok.sum())


def one(arg):
    ex, mk, d, sym, t0, t1 = arg
    base = t0 // MIN
    n = (t1 - t0) // MIN
    if CUBE:
        r = load_cube(os.path.basename(d), sym, t0, t1)
        if r is None or r[2] < 100:
            return [], {}
        cc, qq, _ = r
    else:
        t, h, l, c, q = ev.load_q(os.path.join(d, sym + ".csv.gz"), t0, t1)
        if len(t) < 100:
            return [], {}
        idx = np.fromiter((x // MIN - base for x in t), dtype=np.int64, count=len(t))
        cc = np.full(n, np.nan)
        qq = np.zeros(n)
        cc[idx] = c
        qq[idx] = q
    cq = np.concatenate([[0.0], np.cumsum(qq)])
    lev = bool(LEV.search(sym.split("-")[0]))
    sub = os.path.basename(d)
    hist, rows = {}, []
    xs = CFG["x"]
    for D in CFG["d"]:
        up, dn, lo, hi = moves(cc, D)
        for sgn, mvv, src in ((1, up, lo), (-1, dn, hi)):
            ok = ~np.isnan(mvv)
            if not lev:
                hist[(D, sgn)] = np.bincount(np.searchsorted(EDGES, mvv[ok], side="right") - 1, minlength=len(EDGES))
            xl = xs[(D, sgn)] if isinstance(xs, dict) else xs
            if not xl:
                continue
            xmin = min(xl)
            js = np.nonzero(ok & (mvv >= xmin))[0]
            if not len(js):
                continue
            ist = np.maximum(starts(src, js, D, sgn), 0)
            order = np.argsort(-mvv[js], kind="stable")
            jo, io, vo = js[order].tolist(), ist[order].tolist(), mvv[js][order].tolist()
            # жадно по убыванию хода: решение по кандидату зависит только от прежних, поэтому приёмы при X — префикс (ход ≥ X) приёмов
            # при минимальном X; один проход вместо одного на X. Занятое — непересекающиеся отрезки, проверка пересечения — bisect
            sts, ens, acc = [], [], []
            for j, i, v in zip(jo, io, vo):
                if v < xmin:
                    break
                p = bisect_right(sts, j)
                if p and ens[p - 1] >= i:
                    continue
                sts.insert(p, i)
                ens.insert(p, j)
                ret = cc[j] - (cc[j] - cc[i]) * WICK_FRAC
                post = cc[j + 1:j + 1 + WICK_MIN]
                post = post[~np.isnan(post)]
                wick = int(len(post) > 0 and (post.min() <= ret if sgn > 0 else post.max() >= ret))
                ts, tp = (base + i) * MIN, (base + j) * MIN
                acc.append((v, {"D": D, "биржа": ex, "рынок": mk, "sub": sub, "инструмент": sym, "монета": coin(sym),
                                "знак": "+" if sgn > 0 else "-", "t_start_ms": ts, "t_peak_ms": tp, "t_end_ms": tp + MIN,
                                "длит_мин": j - i, "ход_%": round(float(v) * 100, 3), "E1": int(lev), "E4": wick,
                                "оборот": round(float(cq[j + 1] - cq[i]), 2),
                                "минут_со_свечой": int((~np.isnan(cc[i:j + 1])).sum())}))
            for X in xl:
                rows += [dict(r, X=X) for v, r in acc if v >= X]
    return rows, hist


def quant(hf, outf):
    H = json.load(open(hf))
    res = {}
    for key, cnt in H["hist"].items():
        cnt = np.array(cnt, dtype=np.float64)
        cum = np.cumsum(cnt) / cnt.sum()
        res[key] = {"n_окон": int(cnt.sum())}
        for nm, p in (("q98", 0.98), ("q99", 0.99), ("q99.5", 0.995)):
            res[key][nm] = round(float(EDGES[min(int(np.searchsorted(cum, p)), len(EDGES) - 1)]) * 100, 3)
    json.dump(res, open(outf, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))


def main():
    if sys.argv[1] == "quant":
        return quant(sys.argv[2], sys.argv[3])
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("root")
    ap.add_argument("l4")
    ap.add_argument("art")
    ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=15)
    ap.add_argument("--ym", default="2026-09")
    ap.add_argument("--t0")
    ap.add_argument("--t1")
    ap.add_argument("--x", default="5,10,20,30")
    ap.add_argument("--xq")
    ap.add_argument("--d", default="90,180,360,720,1440")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cube", help="корень куба tk436-cube.py: свечи из куба вместо csv.gz; список инструментов — из корня свечей "
                    "(как без куба), при корне «-» — весь куб")
    a = ap.parse_args()
    global CUBE
    CUBE = a.cube
    if a.art != "-":
        mv.load_excl(a.art)
    if a.l4 != "-":
        for sub, R in json.load(open(a.l4))["рынки"].items():
            for key, info in R.items():
                for p, q in info.get("период_список", []):
                    PER.setdefault((sub, key[-1]), []).append((p, q))
    t0, t1 = mv.month_bounds(a.ym)
    if a.t0:
        t0, t1 = day_ms(a.t0), day_ms(a.t1)
    CFG["d"] = [int(v) for v in a.d.split(",")]
    assert all(D >= MIN_DUR for D in CFG["d"]), "D < %d мин" % MIN_DUR
    if a.x.startswith("q"):
        xq = json.load(open(a.xq))
        CFG["x"] = {(D, s): [xq["%d|%s" % (D, "+" if s > 0 else "-")][a.x] / 100] for D in CFG["d"] for s in (1, -1)}
    else:
        CFG["x"] = [float(v) / 100 for v in a.x.split(",")]
    os.makedirs(a.out, exist_ok=True)
    rows, hist = [], {}
    with Pool(a.procs) as pool:
        for ex, mk, sub in mv.MKT:
            d = os.path.join(a.root, sub)
            if CUBE and a.root == "-":
                syms = sorted(open("%s/%s.syms" % (CUBE, sub)).read().split()) if os.path.exists("%s/%s.syms" % (CUBE, sub)) else []
            else:
                syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
            if a.limit:
                syms = syms[:: max(1, len(syms) // a.limit)][: a.limit]
            nr = 0
            for r, h in pool.imap_unordered(one, [(ex, mk, d, s, t0, t1) for s in syms], chunksize=2):
                rows += r
                nr += len(r)
                for k, v in h.items():
                    hist[k] = hist.get(k, 0) + v
            print(ex, mk, len(syms), "инструментов", nr, "событий (все X, D)", flush=True)
    for r in rows:
        r["X"] = round(r["X"] * 100, 4)
        sg = r["знак"]
        r["E3"] = int(any(p <= r["t_peak_ms"] and q >= r["t_start_ms"] for p, q in PER.get((r["sub"], sg), [])))
        r["исключено"] = ",".join(k for k in ("E1", "E3", "E4") if r[k])
    rows.sort(key=lambda r: (r["D"], r["X"], r["sub"], r["инструмент"], r["t_start_ms"]))
    peaks = {}
    for r in rows:
        r["пара"] = "одиночная"
        if r["знак"] == "+":
            peaks[(r["sub"], r["инструмент"], r["X"], r["D"], r["t_peak_ms"])] = r
    for r in rows:
        pu = peaks.get((r["sub"], r["инструмент"], r["X"], r["D"], r["t_start_ms"])) if r["знак"] == "-" else None
        if pu is not None:
            pu["пара"], r["пара"] = "памп→дамп:памп", "памп→дамп:дамп"
    by = defaultdict(list)
    for r in rows:
        if not r["исключено"]:
            by[(r["D"], r["X"], r["монета"], r["знак"])].append(r)
    cl = 0
    for rs in by.values():
        rs.sort(key=lambda r: r["t_start_ms"])
        cur, end = [], 0
        for r in rs + [None]:
            if r is None or (cur and r["t_start_ms"] > end):
                cl += 1
                rep = max(cur, key=lambda x: x["оборот"])
                for x in cur:
                    x["кластер"], x["представитель"], x["рынков_в_кластере"] = cl, int(x is rep), len(cur)
                cur, end = [], 0
            if r is not None:
                cur.append(r)
                end = max(end, r["t_peak_ms"])
    cols = ["кластер", "представитель", "рынков_в_кластере", "X", "D", "биржа", "рынок", "инструмент", "монета", "знак", "пара",
            "t_start_ms", "t_peak_ms", "t_end_ms", "длит_мин", "ход_%", "оборот", "минут_со_свечой", "E1", "E3", "E4", "исключено"]
    tag = a.ym if not a.t0 else "%s_%s" % (a.t0, a.t1)
    with open(os.path.join(a.out, "windows-%s.csv" % tag), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    fun = defaultdict(lambda: defaultdict(int))
    for r in rows:
        k = "D%d|X%g" % (r["D"], r["X"])
        fun[k]["окон-событий"] += 1
        fun[k]["после E1"] += not r["E1"]
        fun[k]["после E1,E3"] += not (r["E1"] or r["E3"])
        fun[k]["после E1,E3,E4"] += not r["исключено"]
        fun[k]["представителей"] += r.get("представитель", 0)
        fun[k]["в парах памп→дамп"] += r["пара"] != "одиночная"
    json.dump({k: dict(v) for k, v in fun.items()}, open(os.path.join(a.out, "funnel-%s.json" % tag), "w"), ensure_ascii=False, indent=1)
    json.dump({"hist": {"%d|%s" % (D, "+" if s > 0 else "-"): v.tolist() for (D, s), v in hist.items()}, "edges": "linspace(0,1,2001)[:-1]+linspace(1,50,491)"},
              open(os.path.join(a.out, "hist-%s.json" % tag), "w"))
    print(json.dumps({k: dict(v) for k, v in fun.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
