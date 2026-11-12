#!/usr/bin/env python3
"""TK-355 G-015: потолки ручек (сигнал, задержка, выход, отбор) из готовых кэшей касаний — без нового прохода книги.
  g015-ceilings.py <outdir> [--days N]      env NX (по умолчанию 15); --days — проба на первых N сутках каждой монеты
Событие = строка кэша касаний (D20, обе стороны); m_* — markout в сторону отскока, bps; favour/adverse_H — max благоприятного/
неблагоприятного хода за H, bps (шапка кэша, `lob touches`). Страта: источник (fut/spot) × месяц × {первое касание, повторное}
× сила w20 {<100, 100–300, 300–1000, >=1000 %}. Выход: g015-cells.csv (по страте: n, n_days, средние m_h, средние m_h−m_d,
средние favour/adverse_H, доля favour_H >= порога издержек, гистограммы для оракул-отбора), g015.done.
Ничего не читает из rounds: потолки «до правил» (В-213 — только то, чего нет в готовых rounds)."""
import csv, glob, json, math, os, sys, collections
import multiprocessing as mp

MON = "jan feb mar apr may jun jul aug sep".split()
FUT = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/2026-*/touches-*USDT.csv"
SPOT = "/data/tk0319/jrn/2026-*/wt/sub/D20/2026-*/touches-*USDT.csv"
MH = ["m_100ms", "m_1s", "m_10s", "m_60s", "m_10m", "m_1h", "m_2h"]
FAV = ["favour_60s_bps", "favour_600s_bps", "favour_3600s_bps", "favour_7200s_bps"]
ADV = ["adverse_60s_bps", "adverse_600s_bps", "adverse_3600s_bps", "adverse_7200s_bps"]
LAT = [("m_100ms", "m_1s"), ("m_1s", "m_10s"), ("m_10s", "m_60s"), ("m_60s", "m_10m"), ("m_10m", "m_1h")]  # d -> h: m_h - m_d
COST = [11.25, 12.375, 13.5]  # круг bps: maker+maker, maker+taker, taker+taker (тариф В-47)
BIN, CLIP = 1.0, 400


def fl(x):
    try:
        return float(x)
    except ValueError:
        return None


def sbin(w):
    return "lt100" if w < 100 else "100-300" if w < 300 else "300-1000" if w < 1000 else "ge1000"


def work(a):
    src, mon, p = a
    out = {}
    with open(p, newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        ix = {c: i for i, c in enumerate(h)}
        need = MH + FAV + ADV + ["touch_index", "strength_w20_pct", "day_utc"]
        if any(c not in ix for c in need):
            return src, mon, p, None
        for x in r:
            w = fl(x[ix["strength_w20_pct"]])
            if w is None:
                w = 0.0
            ti = "first" if x[ix["touch_index"]] == "0" else "rep"
            k = (src, mon, ti, sbin(w))
            c = out.setdefault(k, {"n": 0, "days": set(), "s": collections.Counter(), "c": collections.Counter(), "hist": {}})
            c["n"] += 1
            c["days"].add(x[ix["day_utc"]])
            v = {n: fl(x[ix[n]]) for n in MH + FAV + ADV}
            for n in MH + FAV + ADV:
                if v[n] is not None:
                    c["s"][n] += v[n]; c["c"][n] += 1
            for d, hh in LAT:
                if v[d] is not None and v[hh] is not None:
                    c["s"][f"{hh}-{d}"] += v[hh] - v[d]; c["c"][f"{hh}-{d}"] += 1
            for n in FAV:
                if v[n] is not None:
                    for i, t in enumerate(COST):
                        if v[n] >= t:
                            c["s"][f"p{n}>={t}"] += 1
                    c["c"][f"p{n}"] += 1
            for n in ("m_10m", "m_1h"):
                if v[n] is not None:
                    b = max(-CLIP, min(CLIP, int(math.floor(v[n] / BIN))))
                    hh_ = c["hist"].setdefault(n, collections.Counter())
                    hh_[b] += 1
    for c in out.values():
        c["days"] = sorted(c["days"])
    return src, mon, p, out


def topk(hist, k):
    tot = sum(hist.values())
    take, acc, s = k * tot, 0.0, 0.0
    for b in sorted(hist, reverse=True):
        m = min(hist[b], take - acc)
        if m <= 0:
            break
        s += m * (b + 0.5) * BIN; acc += m
    return s / acc if acc else None


if __name__ == "__main__":
    out = sys.argv[1]
    nd = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 0
    os.makedirs(out, exist_ok=True)
    jobs = []
    for m in MON:
        by = collections.defaultdict(list)
        for p in glob.glob(FUT.format(m=m)):
            by[os.path.basename(p)].append(p)
        for _, ps in by.items():
            ps.sort()
            jobs += [("fut", m, p) for p in (ps[:nd] if nd else ps)]
    sp = collections.defaultdict(list)
    for p in glob.glob(SPOT):
        sp[os.path.basename(p)].append(p)
    for _, ps in sp.items():
        ps.sort()
        for p in (ps[:nd] if nd else ps):
            jobs.append(("spot", MON[int(p.split("/D20/")[1][5:7]) - 1], p))
    agg = {}
    bad = 0
    with mp.Pool(int(os.environ.get("NX", "15"))) as pool:
        for src, mon, p, o in pool.imap_unordered(work, jobs, chunksize=8):
            if o is None:
                bad += 1
                continue
            for k, c in o.items():
                a = agg.setdefault(k, {"n": 0, "days": set(), "s": collections.Counter(), "c": collections.Counter(), "hist": {}})
                a["n"] += c["n"]; a["days"].update(c["days"]); a["s"].update(c["s"]); a["c"].update(c["c"])
                for n, hh in c["hist"].items():
                    a["hist"].setdefault(n, collections.Counter()).update(hh)
    cols = (MH + [f"{h}-{d}" for d, h in LAT] + FAV + ADV
            + [f"p{n}>={t}" for n in FAV for t in COST])
    with open(os.path.join(out, "g015-cells.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src", "month", "touch", "strength", "n", "n_days"] + cols
                   + [f"top{int(k*100)}_{n}" for n in ("m_10m", "m_1h") for k in (0.05, 0.1, 0.25, 0.5)])
        for k in sorted(agg):
            a = agg[k]
            row = list(k) + [a["n"], len(a["days"])]
            for n in cols:
                if n.startswith("p"):
                    base = n.split(">=")[0]
                    cn = a["c"].get(base)
                    row.append(round(a["s"].get(n, 0) / cn, 4) if cn else "")
                else:
                    cn = a["c"].get(n)
                    row.append(round(a["s"][n] / cn, 3) if cn else "")
            for n in ("m_10m", "m_1h"):
                for q in (0.05, 0.1, 0.25, 0.5):
                    t = topk(a["hist"].get(n, {}), q) if a["hist"].get(n) else None
                    row.append(round(t, 2) if t is not None else "")
            w.writerow(row)
    json.dump({"jobs": len(jobs), "bad_header_files": bad, "cells": len(agg)}, open(os.path.join(out, "g015-summary.json"), "w"))
    open(os.path.join(out, "g015.done"), "w").write("ok\n")
