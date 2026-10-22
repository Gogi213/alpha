#!/usr/bin/env python3
"""TK-361 G-015: сигнал S (markout mid после касания, бп, в сторону отскока) по корзинам размера стены (USD) и возраста стены,
из готовых кэшей касаний D20 (как g015-ceilings.py; нового прохода книги нет). USD = price_tick·tick·size_at_touch·lot, тик и лот
ТОЛЬКО из заголовка бинлога суток (байты 5..21, i64 LE; не instruments.csv — TK-360).
  tk361-strata.py <outdir> [--days N]     env NX (15)
Выход: strata.csv (страта × клетка: n, n_days, n_coins, S по горизонтам, интервал по суткам, топ-3 монет), tk361.done."""
import csv, glob, json, math, os, re, struct, sys, collections
import multiprocessing as mp

MON = "jan feb mar apr may jun jul aug sep".split()
FUT = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/2026-*/touches-*USDT.csv"
SPOT = "/data/tk0319/jrn/2026-*/wt/sub/D20/2026-*/touches-*USDT.csv"
ROOT = {"fut": "/data/tk046/{m}/home/alpha/epochs/e-{m}/root", "spot": "/data/tk0319/e-{m}/root"}
H = ["m_1s", "m_10s", "m_60s", "m_10m", "m_1h", "m_2h"]
SZ = [25e3, 50e3, 100e3, 200e3, 500e3, 1e6]
AG = [60e3, 600e3, 2700e3, 7200e3, 21600e3]  # мс: 1 мин, 10 мин, 45 мин, 2 ч, 6 ч
STABLE = re.compile(r"^(USDC|USD1|FDUSD|USDE|TUSD|DAI|PYUSD|USDD|RLUSD|USDT0|EUR|XUSD)USDT$")
NH = len(H)


def fl(x):
    try:
        return float(x)
    except ValueError:
        return None


def bk(v, edges):
    for i, e in enumerate(edges):
        if v < e:
            return i
    return len(edges)


def hdr(src, mon, sym, day):
    p = f"{ROOT[src].format(m=mon)}/{sym}-{day}.binlog"
    try:
        with open(p, "rb") as f:
            b = f.read(21)
        if b[:4] != b"ABLG":
            return None
        t, l = struct.unpack("<qq", b[5:21])
        return t / 1e9, l / 1e9
    except OSError:
        return None


def work(a):
    src, mon, p = a
    sym = os.path.basename(p)[len("touches-"):-4]
    out = {}
    miss = 0
    with open(p, newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        ix = {c: i for i, c in enumerate(h)}
        if any(c not in ix for c in H + ["touch_index", "price_tick", "size_at_touch", "age_ms", "day_utc"]):
            return src, mon, sym, None, 0
        g = None
        for x in r:
            day = x[ix["day_utc"]]
            if g is None:
                g = hdr(src, mon, sym, day)
                if g is None:
                    return src, mon, sym, None, 0
            pt, sz, ag = fl(x[ix["price_tick"]]), fl(x[ix["size_at_touch"]]), fl(x[ix["age_ms"]])
            if pt is None or sz is None or ag is None:
                miss += 1
                continue
            usd = pt * g[0] * sz * g[1]
            k = (src, mon, "first" if x[ix["touch_index"]] == "0" else "rep",
                 "stable" if STABLE.match(sym) else "alt", bk(usd, SZ), bk(ag, AG))
            c = out.setdefault(k, [day, 0] + [0.0] * NH + [0] * NH)
            c[1] += 1
            for i, n in enumerate(H):
                v = fl(x[ix[n]])
                if v is not None:
                    c[2 + i] += v; c[2 + NH + i] += 1
    return src, mon, sym, out, miss


def add(d, key, row):
    t = d.setdefault(key, [0] * (1 + 2 * NH))
    for i, v in enumerate(row):
        t[i] += v


if __name__ == "__main__":
    out = sys.argv[1]
    nd = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 0
    os.makedirs(out, exist_ok=True)
    jobs = []
    for m in MON:
        by = collections.defaultdict(list)
        for p in glob.glob(FUT.format(m=m)):
            by[os.path.basename(p)].append(p)
        for ps in by.values():
            ps.sort()
            jobs += [("fut", m, p) for p in (ps[:nd] if nd else ps)]
    sp = collections.defaultdict(list)
    for p in glob.glob(SPOT):
        sp[os.path.basename(p)].append(p)
    for ps in sp.values():
        ps.sort()
        for p in (ps[:nd] if nd else ps):
            jobs.append(("spot", MON[int(p.split("/D20/")[1][5:7]) - 1], p))
    agg = {}
    bad = miss = 0
    with mp.Pool(int(os.environ.get("NX", "15"))) as pool:
        for src, mon, sym, o, ms in pool.imap_unordered(work, jobs, chunksize=8):
            miss += ms
            if o is None:
                bad += 1
                continue
            for k, c in o.items():
                a = agg.setdefault(k, {"day": {}, "sym": {}})
                add(a["day"], c[0], c[1:]); add(a["sym"], sym, c[1:])
    full = {}
    for k, a in agg.items():
        for kk in (k, k[:4] + ("*", k[5]), k[:4] + (k[4], "*"), k[:4] + ("*", "*")):
            f = full.setdefault(kk, {"day": {}, "sym": {}})
            for part in ("day", "sym"):
                for key, row in a[part].items():
                    add(f[part], key, row)
    cols = ["src", "month", "touch", "group", "size_b", "age_b", "n", "n_days", "n_coins", "top3_n_share"]
    for n in H:
        cols += [f"S_{n}", f"lo_{n}", f"hi_{n}", f"S_ex3_{n}"]
    with open(os.path.join(out, "strata.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols)
        for k in sorted(full, key=str):
            a = full[k]
            tot = [0] * (1 + 2 * NH)
            for row in a["sym"].values():
                for i, v in enumerate(row):
                    tot[i] += v
            n = tot[0]
            top = sorted(a["sym"].items(), key=lambda kv: -kv[1][0])[:3]
            row = list(k) + [n, len(a["day"]), len(a["sym"]), round(sum(r[0] for _, r in top) / n, 4) if n else ""]
            for i in range(NH):
                c, s = tot[1 + NH + i], tot[1 + i]
                S = s / c if c else None
                dm = [r[1 + i] / r[1 + NH + i] for r in a["day"].values() if r[1 + NH + i]]
                if len(dm) > 1:
                    mu = sum(dm) / len(dm)
                    se = math.sqrt(sum((x - mu) ** 2 for x in dm) / (len(dm) - 1) / len(dm))
                    lo, hi = mu - 1.96 * se, mu + 1.96 * se
                else:
                    lo = hi = None
                cx = c - sum(r[1 + NH + i] for _, r in top)
                sx = s - sum(r[1 + i] for _, r in top)
                row += [round(S, 3) if S is not None else "", round(lo, 3) if lo is not None else "",
                        round(hi, 3) if hi is not None else "", round(sx / cx, 3) if cx > 0 else ""]
            w.writerow(row)
    json.dump({"jobs": len(jobs), "bad_files": bad, "rows_missing_fields": miss, "cells": len(full)},
              open(os.path.join(out, "summary.json"), "w"))
    open(os.path.join(out, "tk361.done"), "w").write("ok\n")
