#!/usr/bin/env python3
"""TK-363 G-015: движок псевдотеста — «событие × условие → markout» по готовому кэшу касаний D20 одним проходом по файлам
для ВСЕХ гипотез сразу (В-136). Новая гипотеза = строка JSON-конфига, не код.
  pseudotest.py <config.json> <outdir> [--days N]      env NX (по умолчанию 15)
Конфиг: {"seed": int, "boot": int, "caches": {"event": {"fut": "<glob с {m}>", "spot": "<glob>"}, "c3": {...}},
         "roots": {"fut": "<корень с {m}>", "spot": "<корень с {m}>"},       # только для usd (тик/лот из заголовка бинлога)
         "hypotheses": [{"id": str, "where": "<выражение по колонкам кэша>", "sign": 1|-1, "note": str}]}
Событие = строка кэша; m_* — markout mid в сторону отскока, бп; sign=-1 — гипотеза «против отскока» (m → −m, favour ↔ adverse).
В where: колонки шапки кэша, производные first/rep (touch_index), w20 (strength_w20_pct, пусто → 0, как g015-ceilings), usd
(price_tick·тик·size_at_touch·лот, только из заголовка бинлога — TK-360); and/or/not, сравнения, + − * /, abs(), isnan().
Популяция "event" — касания; любая другая из "caches" (напр. c3 — контроль «случайные уровни») считается тем же условием,
S_net = S_event − S_ctrl в той же клетке. Клетка = гипотеза × популяция × рынок × месяц; месяц фьючерса — по каталогу эпохи
({m}), спота — по D20/2026-MM в пути (как g015-ceilings). Выход: pseudotest-cells.csv, pseudotest-summary.json, pseudotest.done.
Клетка: n, n_days, n_coins, доля топ-3 монет по n, S по горизонтам (m_100ms…m_2h), интервал бутстрапа по суткам (B=boot,
сид = seed + crc32(ключ)), S без топ-3 монет, favour/adverse (60 с…2 ч). Всё считается до округления (6 знаков)."""
import ast, csv, glob, json, os, re, struct, sys, zlib, collections
import multiprocessing as mp
import numpy as np
import pandas as pd

MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()
MH = ["m_100ms", "m_1s", "m_10s", "m_60s", "m_10m", "m_1h", "m_2h"]
FAV = ["favour_60s_bps", "favour_600s_bps", "favour_3600s_bps", "favour_7200s_bps"]
ADV = ["adverse_60s_bps", "adverse_600s_bps", "adverse_3600s_bps", "adverse_7200s_bps"]
VAL = MH + FAV + ADV  # значения клетки: суммы и счётчики по этим колонкам
NV = len(VAL)
BASE = ["touch_index", "strength_w20_pct", "day_utc"]
DERIVED = {"first", "rep", "w20", "usd"}
FUNCS = {"abs": np.abs, "isnan": np.isnan}


def parse(expr):
    """where → (вычислитель(env)->bool-массив, множество имён). Только безопасный набор узлов ast."""
    tree = ast.parse(expr, mode="eval").body
    names = set()

    def ev(n, env):
        if isinstance(n, ast.BoolOp):
            f = np.logical_and if isinstance(n.op, ast.And) else np.logical_or
            r = ev(n.values[0], env)
            for v in n.values[1:]:
                r = f(r, ev(v, env))
            return r
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand, env)
            if isinstance(n.op, ast.Not):
                return np.logical_not(v)
            return -v if isinstance(n.op, ast.USub) else +v
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left, env), ev(n.right, env)
            return {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}[type(n.op)](a, b)
        if isinstance(n, ast.Compare):
            ops = {ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater, ast.GtE: np.greater_equal,
                   ast.Eq: np.equal, ast.NotEq: np.not_equal}
            r, left = None, ev(n.left, env)
            for o, c in zip(n.ops, n.comparators):
                right = ev(c, env)
                t = ops[type(o)](left, right)
                r = t if r is None else np.logical_and(r, t)
                left = right
            return r
        if isinstance(n, ast.Name):
            return env[n.id]
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and len(n.args) == 1:
            return FUNCS[n.func.id](ev(n.args[0], env))
        raise ValueError(f"where: недопустимый узел {type(n).__name__} в «{expr}»")

    def collect(n):
        for c in ast.walk(n):
            if isinstance(c, ast.Name):
                names.add(c.id)
            if isinstance(c, ast.Call):
                names.discard(c.func.id)
    collect(tree)
    ev(tree, {k: np.zeros(1) for k in names})  # проба: синтаксис и узлы проверены до прохода
    return (lambda env: ev(tree, env)), names


def hdr(root, sym, day):
    try:
        with open(f"{root}/{sym}-{day}.binlog", "rb") as f:
            b = f.read(21)
        if b[:4] != b"ABLG":
            return None
        t, l = struct.unpack("<qq", b[5:21])
        return t / 1e9, l / 1e9
    except OSError:
        return None


CFG = None  # заполняется в init() воркера


def init(cfg):
    global CFG
    CFG = cfg
    CFG["_hyp"] = []
    for h in cfg["hypotheses"]:
        f, names = parse(h["where"])
        CFG["_hyp"].append((h["id"], f, names, float(h.get("sign", 1))))
    need = set()
    for _, _, names, _ in CFG["_hyp"]:
        need |= names
    CFG["_need"] = need
    CFG["_cols"] = sorted((need - DERIVED) | set(VAL) | set(BASE) | ({"price_tick", "size_at_touch"} if "usd" in need else set()))


def work(a):
    pop, src, mon, p = a
    sym = os.path.basename(p)[len("touches-"):-4]
    try:
        df = pd.read_csv(p, usecols=CFG["_cols"], dtype={"day_utc": str})
    except ValueError:
        return a, None
    if len(df) == 0:
        return a, []
    env = {c: df[c].to_numpy(dtype=float) for c in CFG["_cols"] if c != "day_utc"}
    ti = env["touch_index"]
    env["first"] = (ti == 0).astype(float)
    env["rep"] = (ti != 0).astype(float)
    w = env["strength_w20_pct"].copy()
    w[np.isnan(w)] = 0.0
    env["w20"] = w
    days = df["day_utc"].to_numpy()
    if "usd" in CFG["_need"]:
        out_nan = np.full(len(df), np.nan)
        usd = out_nan.copy()
        for d in np.unique(days):
            g = hdr(CFG["roots"][src].format(m=mon), sym, d)
            if g is not None:
                m = days == d
                usd[m] = env["price_tick"][m] * g[0] * env["size_at_touch"][m] * g[1]
        env["usd"] = usd
    vals = np.stack([env[c] for c in VAL], axis=1)
    ok = ~np.isnan(vals)
    z = np.where(ok, vals, 0.0)
    out = []
    ud, inv = np.unique(days, return_inverse=True)
    for hi, (hid, f, _, sign) in enumerate(CFG["_hyp"]):
        mask = np.asarray(f(env), dtype=bool)
        if mask.shape == ():
            mask = np.full(len(df), bool(mask))
        if not mask.any():
            continue
        zz, oo = z[mask], ok[mask]
        if sign < 0:  # против отскока: markout со знаком, favour ↔ adverse
            zz = zz.copy()
            zz[:, :len(MH)] *= -1
            zz[:, len(MH):] = np.concatenate([zz[:, len(MH) + 4:], zz[:, len(MH):len(MH) + 4]], axis=1)
            oo = np.concatenate([oo[:, :len(MH)], oo[:, len(MH) + 4:], oo[:, len(MH):len(MH) + 4]], axis=1)
        dm = inv[mask]
        for k in np.unique(dm):
            s = dm == k
            out.append((hi, str(ud[k]), np.concatenate([[s.sum()], zz[s].sum(axis=0), oo[s].sum(axis=0)])))
    return a, out


def merge(agg, key, day, sym, vec):
    a = agg.get(key)
    if a is None:
        a = agg[key] = {"day": {}, "sym": {}}
    for part, k in (("day", day), ("sym", sym)):
        t = a[part].get(k)
        if t is None:
            a[part][k] = vec.copy()
        else:
            t += vec


def cell(a, key, B, seed):
    """Итог клетки из сумм по суткам и по монетам."""
    tot = sum(a["sym"].values())
    n = tot[0]
    top = sorted(a["sym"].items(), key=lambda kv: -kv[1][0])[:3]
    row = {"n": int(n), "n_days": len(a["day"]), "n_coins": len(a["sym"]), "top3_n_share": sum(v[0] for _, v in top) / n}
    S = np.divide(tot[1:1 + NV], tot[1 + NV:], out=np.full(NV, np.nan), where=tot[1 + NV:] > 0)
    ex = tot - sum(v for _, v in top)
    Sx = np.divide(ex[1:1 + NV], ex[1 + NV:], out=np.full(NV, np.nan), where=ex[1 + NV:] > 0)
    D = np.array(list(a["day"].values()))
    rng = np.random.default_rng(seed + zlib.crc32("|".join(key).encode()))
    lo = np.full(NV, np.nan)
    hi = np.full(NV, np.nan)
    if len(D) > 1 and B:
        idx = rng.integers(0, len(D), size=(B, len(D)))
        sb = D[idx, 1:1 + NV].sum(axis=1)
        cb = D[idx, 1 + NV:].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            bs = sb / cb
        lo, hi = np.nanpercentile(bs, 2.5, axis=0), np.nanpercentile(bs, 97.5, axis=0)
    for i, c in enumerate(VAL):
        row[f"S_{c}"] = S[i]
        if c in MH:
            row[f"lo_{c}"], row[f"hi_{c}"], row[f"Sex3_{c}"] = lo[i], hi[i], Sx[i]
    return row


def jobs_for(cfg, nd):
    jobs = []
    for pop, spec in cfg["caches"].items():
        for src, g in spec.items():
            if "{m}" in g:
                for m in MON:
                    by = collections.defaultdict(list)
                    for p in glob.glob(g.format(m=m)):
                        by[os.path.basename(p)].append(p)
                    for ps in by.values():
                        ps.sort()
                        jobs += [(pop, src, m, p) for p in (ps[:nd] if nd else ps)]
            else:
                by = collections.defaultdict(list)
                for p in glob.glob(g):
                    by[os.path.basename(p)].append(p)
                for ps in by.values():
                    ps.sort()
                    for p in (ps[:nd] if nd else ps):
                        jobs.append((pop, src, MON[int(re.search(r"/D20/\d{4}-(\d\d)", p.replace("\\", "/")).group(1)) - 1], p))
    return jobs


def main():
    cfg = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    nd = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 0
    os.makedirs(out, exist_ok=True)
    init(json.loads(json.dumps(cfg)))  # проверка конфига до прохода; воркеры получают чистый cfg (spawn)
    ids = [h["id"] for h in cfg["hypotheses"]]
    assert len(set(ids)) == len(ids), "id гипотез повторяются"
    jobs = jobs_for(cfg, nd)
    agg, bad = {}, 0
    with mp.Pool(int(os.environ.get("NX", "15")), initializer=init, initargs=(cfg,)) as pool:
        for (pop, src, mon, p), o in pool.imap_unordered(work, jobs, chunksize=8):
            if o is None:
                bad += 1
                continue
            sym = os.path.basename(p)[len("touches-"):-4]
            for hi, day, vec in o:
                merge(agg, (ids[hi], pop, src, mon), day, sym, vec)
    B, seed = int(cfg.get("boot", 1000)), int(cfg.get("seed", 20261022))
    rows = {k: cell(a, k, B, seed) for k, a in agg.items()}
    hs = [c for c in rows[next(iter(rows))] if c.startswith("S_m_")] if rows else []
    for k, r in rows.items():
        c = rows.get((k[0], "c3", k[2], k[3])) if k[1] == "event" else None
        for col in hs:
            r["Snet_" + col[2:]] = r[col] - c[col] if c else float("nan")
    cols = ["hyp", "pop", "src", "month"] + (list(next(iter(rows.values())).keys()) if rows else [])
    with open(os.path.join(out, "pseudotest-cells.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for k in sorted(rows, key=lambda k: (ids.index(k[0]), k[1], k[2], MON.index(k[3]))):
            w.writerow(list(k) + [repr(float(rows[k][c])) if isinstance(rows[k][c], (float, np.floating)) else rows[k][c] for c in cols[4:]])
    hashes = len(ids) * sum(len(v) for v in cfg["caches"].values())
    json.dump({"jobs": len(jobs), "bad_header_files": bad, "hypotheses": len(ids), "cells": len(rows),
               "hashes_hyp_x_pop_x_src_x_month": len(rows), "hyp_x_pop_x_src": hashes}, open(os.path.join(out, "pseudotest-summary.json"), "w"))
    open(os.path.join(out, "pseudotest.done"), "w").write("ok\n")


if __name__ == "__main__":
    main()
