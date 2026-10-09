"""Библиотека слоя разбора П-12 (TK-135, С-59/С-60): загрузчик голов r1/r2/tk083, pack, sr, block_idx, analyse — вынесено из p12-sharpe.py.
Окружение читается при импорте: P12_POOL_FROM (В-211: янв–сен — 1), P12_ONLY, P12_OUT — выставлять ДО import p12lib."""
import math, os, sys
from collections import defaultdict
import numpy as np

M_FAM, B = 42, 20000
POOL = {f"2026-{i:02d}" for i in range(int(os.environ.get("P12_POOL_FROM", 2)), 10)}   # В-211: окно янв–сен — P12_POOL_FROM=1
ONLY_R2 = os.environ.get("P12_ONLY") in ("r2", "r1r2")   # r1r2 — R1 и R2 без ext
OUT = os.environ.get("P12_OUT", "2026-10-08")


def load_head(path, marker, argv):
    """Единственное место, где «голова» скрипта-источника исполняется до маркера (С-59): argv на время загрузки подменяется и возвращается."""
    src = open(path, encoding="utf-8").read()
    cut = src.index(marker)
    argv0, sys.argv = sys.argv, ["x", *argv]
    ns = {"__name__": "x"}
    try:
        exec(compile(src[:cut], path, "exec"), ns)
    finally:
        sys.argv = argv0
    return ns


def load(path, mode):
    marker = "S = {c: series(c) for c in raw}" if "p12-r1" in path else "rng = np.random.default_rng(63)"
    return load_head(path, marker, [mode])


def pack(ns, r1):
    """-> {имя: dict(arr, lst{мес:[(мс,$)]}, dfn[мес], fam, base, cal)}"""
    out = {}
    if r1:
        S = {c: ns["series"](c) for c in ns["raw"]}
        for c, s in S.items():
            out[c] = dict(arr=s[0], lst=ns["raw"][c], dfn=set(ns["defined_of"](s[1], s[2])), fam=c.split("-")[0] if c != "B1" else "B1",
                          base=None if c == "B1" else "B1", cal=ns["cal"], months=ns["MONTHS"], kpi=ns["kpi_share"])
    else:
        S = ns["S"]
        for c, s in S.items():
            f = ns["CELLS"][c][0] if c in ns["CELLS"] else c
            base = None if c in ("B1", "B3", "B1g") else ns["BN"][ns["CELLS"][c][1]]
            out[c] = dict(arr=s[0], lst=s[3], dfn=set(ns["defined_of"](s[1], s[2], s[4] if len(s) > 4 else None)), fam=f, base=base,
                          cal=ns["cal"], months=ns["MONTHS"], kpi=ns["kpi_share"])
    return out


def pack_ext(mode):
    """ext-клетки TK-083 (7 кл., x-stop/x-deadline/x-entry): closes data/tk083/kpi/closes[B2]-<мес>.json; $ без нормировки (t2 ≤ 1,25 × B1, вердикт ext)."""
    ns = load_head("tools/compute/tk083-kpi-analyze.py", "rng = np.random.default_rng(83)", ["" if mode == "free" else "B2"])
    out = {}
    for c, a in ns["daily"].items():
        out[c] = dict(arr=a, lst=ns["cl"][c], dfn=set(ns["defined"][c]), fam="B1" if c == "B1" else ns["CELLS"][c][0], base=None if c == "B1" else "B1",
                      cal=ns["cal"], months=ns["MONTHS"], kpi=lambda l, m, k=ns["kpi_share"]: (lambda r: (r[0], r[2]))(k(l, m)))
    return out


def sr(x, m):
    n = m.sum(-1)
    mean = (x * m).sum(-1) / np.maximum(n, 1)
    var = (((x - mean[..., None]) ** 2) * m).sum(-1) / np.maximum(n - 1, 1)
    sd = np.sqrt(var)
    return np.where(sd > 0, mean / np.where(sd > 0, sd, 1), 0.0)


def block_idx(rng, n):
    L = max(1, math.ceil(n ** (1 / 3))); nb = math.ceil(n / L)
    return ((rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(L)) % n).reshape(B, -1)[:, :n]


def analyse(P, seed):
    rng = np.random.default_rng(seed)
    cal0 = next(iter(P.values()))["cal"]; months = [m for m in next(iter(P.values()))["months"] if m in POOL]
    keep = np.array([d[:7] in POOL for d in cal0])   # §4: пул счёта фев–сен; январь — калибровка, октябрь — неполный
    cal = [d for d, k in zip(cal0, keep) if k]; n = len(cal)
    mon = np.array([d[:7] for d in cal])
    for p in P.values():
        p["arr"] = p["arr"][keep]; p["dfn"] = p["dfn"] & POOL
    idx = block_idx(rng, n)
    midx = {}
    for m in months:
        pos = np.where(mon == m)[0]
        if len(pos):
            midx[m] = (pos, pos[block_idx(rng, len(pos))])
    res = {}
    for c, p in P.items():
        if p["base"] is None:
            continue
        b = P[p["base"]]
        dm = p["dfn"] & b["dfn"]
        mk = np.isin(mon, list(dm))
        if mk.sum() < 20:
            res[c] = None; continue
        x, y = p["arr"], b["arr"]
        sc, sb = float(sr(x, mk.astype(float))), float(sr(y, mk.astype(float)))
        xs, ys, ms = x[idx], y[idx], mk[idx].astype(float)
        d = sr(xs, ms) - sr(ys, ms); dsr = sc - sb
        se = d.std(ddof=1) or 1e-9
        r = dict(c=c, fam=p["fam"], base=p["base"], mk=mk, dm=dm, sr=sc, srb=sb, dsr=dsr, se=se, T=dsr / se, Ts=(d - dsr) / se,
                 lo=np.percentile(d, 2.5), hi=np.percentile(d, 97.5))
        mres = {}
        for m in sorted(dm):
            pos, mi = midx[m]
            ones = np.ones(mi.shape)
            dd = sr(x[mi], ones) - sr(y[mi], ones)
            one = np.ones(len(pos))
            mres[m] = (float(sr(x[pos], one) - sr(y[pos], one)), np.percentile(dd, 97.5), float(sr(x[pos], one)))
        r["mon"] = mres
        r["k"] = sum(1 for m in mres if mres[m][2] > 0)
        diff = np.where(mk, x - y, -1e18); top = np.argsort(-diff)[:2]
        mk2 = mk.copy(); mk2[top] = False
        s2c, s2b = sr(x, mk2.astype(float)), sr(y, mk2.astype(float))
        m2 = mk2[idx].astype(float)
        d2 = sr(xs, m2) - sr(ys, m2)
        r["c2"] = (float(s2c - s2b), np.percentile(d2, 2.5))
        w = 0; bad = 0.0
        for m in sorted(dm):
            s, _eq = p["kpi"](p["lst"].get(m, []), m); wt = (mon == m).sum(); bad += s * wt; w += wt
        r["K"] = bad / max(w, 1)
        r["usd"] = sum(v for m in dm for _a, v in p["lst"].get(m, [])); r["usdb"] = sum(v for m in dm for _a, v in b["lst"].get(m, []))
        res[c] = r
    ok = [c for c in res if res[c]]
    fam = defaultdict(list)
    for c in ok:
        fam[res[c]["fam"]].append(c)
    for f, cs in fam.items():
        for key, sgn in (("pwy", -1), ("pharm", 1)):
            order = sorted(cs, key=lambda c: sgn * res[c]["T"]); prev = 0.0
            for k, c in enumerate(order):
                ts = np.array([res[o]["Ts"] for o in order[k:]])
                pv = float(((ts.max(0) >= res[c]["T"]) if sgn < 0 else (ts.min(0) <= res[c]["T"])).mean())
                prev = max(prev, pv); res[c][key] = prev
    return res, fam
