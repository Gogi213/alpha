#!/usr/bin/env python3
"""TK-363 G-015: движок псевдотеста — «событие × условие → markout» по готовому кэшу касаний D20 одним проходом по файлам
для ВСЕХ гипотез сразу (В-136). Новая гипотеза = строка JSON-конфига, не код.
  pseudotest.py <config.json> <outdir> [--days N]      env NX (по умолчанию 15)
Конфиг: {"seed": int, "boot": int, "caches": {"event": {"fut": "<glob с {m}>", "spot": "<glob>"}, "c3": {...}},
         "roots": {"fut": "<корень с {m}>", "spot": "<корень с {m}>"},       # только для usd (тик/лот из заголовка бинлога)
         "hypotheses": [{"id": str, "where": "<выражение по колонкам кэша>", "sign": 1|-1, "note": str}]}
Событие = строка кэша; m_* — markout mid в сторону отскока, бп; sign=-1 — гипотеза «против отскока» (m → −m, favour ↔ adverse).
В where: колонки шапки кэша, производные first/rep (touch_index), fut (рынок файла 1/0), alt (монета не стейбл), w20 (strength_w20_pct, пусто → 0, как g015-ceilings), usd
(price_tick·тик·size_at_touch·лот, только из заголовка бинлога — TK-360); and/or/not, сравнения, + − * /, abs(), isnan().
Популяция "event" — касания; любая другая из "caches" (напр. c3 — контроль «случайные уровни») считается тем же условием,
S_net = S_event − S_ctrl в той же клетке. Клетка = гипотеза × популяция × рынок × месяц; месяц фьючерса — по каталогу эпохи
({m}), спота — по D20/2026-MM в пути (как g015-ceilings). Выход: pseudotest-cells.csv, pseudotest-summary.json, pseudotest.done.
Клетка: n, n_days, n_coins, доля топ-3 монет по n, S по горизонтам (m_100ms…m_2h), интервал бутстрапа по суткам (B=boot,
сид = seed + crc32(ключ)), S без топ-3 монет, favour/adverse (60 с…2 ч); распределение хода к 10 с / 1 мин / 10 мин / 1 ч: доли up (>0, отскок) /
dn (<0, пробой), p10 / med / p90 бп (гистограмма asinh-корзин, шаг 0,02 — погрешность ~2 % величины); h_src=tick. Средние до округления."""
import ast, csv, glob, json, os, re, struct, sys, time, zlib, collections
import multiprocessing as mp
import numpy as np

MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()
MH = ["m_100ms", "m_1s", "m_10s", "m_60s", "m_10m", "m_1h", "m_2h"]
FAV = ["favour_60s_bps", "favour_600s_bps", "favour_3600s_bps", "favour_7200s_bps"]
ADV = ["adverse_60s_bps", "adverse_600s_bps", "adverse_3600s_bps", "adverse_7200s_bps"]
VAL = MH + FAV + ADV  # значения клетки: суммы и счётчики по этим колонкам
NV = len(VAL)
HD = ["m_10s", "m_60s", "m_10m", "m_1h"]  # горизонты распределения хода (медиана, p10/p90, доли вверх/вниз)
HI = [VAL.index(c) for c in HD]
STEP = 0.02  # корзина гистограммы — равномерная по asinh(|x|, бп): точно у нуля, ~2 % на 100 бп; ноль — отдельная корзина
KM = int(np.ceil(np.arcsinh(5000.0) / STEP))  # |ход| > 5000 бп — в крайнюю корзину
NB = 2 * KM + 1
BASE = ["touch_index", "strength_w20_pct", "day_utc"]
DERIVED = {"first", "rep", "w20", "usd", "fut", "alt"}
STABLE = re.compile(r"^(USDC|USD1|FDUSD|USDE|TUSD|DAI|PYUSD|USDD|RLUSD|USDT0|EUR|XUSD)USDT$")  # как tk361-strata
FUNCS = {"abs": np.abs, "isnan": np.isnan}


QCALL = re.compile(r"^q(\d+)$")


def parse(expr):
    """where → (вычислитель(env)->bool-массив, множество имён, ключи q-термов). Только безопасный набор узлов ast."""
    tree = ast.parse(expr, mode="eval").body
    names = set()
    qkeys = []

    def qkey(n):
        p = int(QCALL.match(n.func.id).group(1))
        assert 1 <= len(n.args) <= 2 and 0 < p < 100, f"where: qNN(признак[, маска]) в «{expr}»"
        return f"q{p}|{ast.unparse(n.args[0])}|{ast.unparse(n.args[1]) if len(n.args) == 2 else ''}"

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
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and QCALL.match(n.func.id):
            return env["_" + qkey(n)]  # порог монеты за предыдущие 7 суток — скаляр, кладёт воркер
        raise ValueError(f"where: недопустимый узел {type(n).__name__} в «{expr}»")

    def collect(n):
        funcs = set()
        for c in ast.walk(n):
            if isinstance(c, ast.Name):
                names.add(c.id)
            if isinstance(c, ast.Call):
                funcs.add(c.func.id)
                if QCALL.match(c.func.id):
                    qkeys.append(qkey(c))
        names.difference_update(funcs)  # имена функций — не колонки (BFS видит Name после Call)
    collect(tree)
    probe = {k: np.zeros(1) for k in names}
    probe.update({"_" + k: 0.0 for k in qkeys})
    ev(tree, probe)  # проба: синтаксис и узлы проверены до прохода
    return (lambda env: ev(tree, env)), names, sorted(set(qkeys))


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
    need, qk = set(), set()
    for h in cfg["hypotheses"]:
        f, names, qs = parse(h["where"])
        CFG["_hyp"].append((h["id"], f, names, float(h.get("sign", 1)), qs))
        need |= names
        qk |= set(qs)
    CFG["_need"] = need
    CFG["_qk"] = sorted(qk)  # q-термы «qNN|признак|маска»: признак и маска считаются по строкам файла монеты
    CFG["_qf"] = {}
    for k in CFG["_qk"]:
        _, feat, mask = k.split("|")
        CFG["_qf"][k] = (int(k[1:k.index("|")]), parse(feat)[0], parse(mask)[0] if mask else None)
    CFG["_cols"] = sorted((need - DERIVED) | set(VAL) | set(BASE) | ({"price_tick", "size_at_touch"} if "usd" in need else set()))


def load(src, mon, sym, p):
    """Файл (монета, сутки) → (env, days, n_rows); None — нет колонок; n_rows 0 — пусто."""
    with open(p, newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        ix = {c: i for i, c in enumerate(h)}
        if any(c not in ix for c in CFG["_cols"]):
            return None
        rows = list(r)
    if not rows:
        return {}, None, 0
    cols = list(zip(*rows))
    env = {c: np.array([v or "nan" for v in cols[ix[c]]], dtype=float) for c in CFG["_cols"] if c != "day_utc"}
    days = np.array(cols[ix["day_utc"]])
    n_rows = len(rows)
    ti = env["touch_index"]
    env["first"] = (ti == 0).astype(float)
    env["rep"] = (ti != 0).astype(float)
    env["fut"] = np.full(n_rows, float(src == "fut"))  # рынок файла: пороги «фьюч / спот» одной строкой
    env["alt"] = np.full(n_rows, 0.0 if STABLE.match(sym) else 1.0)  # не стейбл (шкала не в $)
    w = env["strength_w20_pct"].copy()
    w[np.isnan(w)] = 0.0
    env["w20"] = w
    if "usd" in CFG["_need"]:
        usd = np.full(n_rows, np.nan)
        for d in np.unique(days):
            g = hdr(CFG["roots"][src].format(m=mon), sym, d)
            if g is not None:
                m = days == d
                usd[m] = env["price_tick"][m] * g[0] * env["size_at_touch"][m] * g[1]
        env["usd"] = usd
    return env, days, n_rows


def feats(o):
    """Значения q-признаков файла (конечные, по маске) — для порога монеты по окну суток."""
    if o is None or o[2] == 0:
        return {k: np.empty(0) for k in CFG["_qk"]}
    env, _, n_rows = o
    out = {}
    for k, (_, ff, mf) in CFG["_qf"].items():
        v = np.broadcast_to(np.asarray(ff(env), dtype=float), (n_rows,))
        m = np.isfinite(v)
        if mf is not None:
            m = m & np.broadcast_to(np.asarray(mf(env), dtype=bool), (n_rows,))
        out[k] = v[m]
    return out


def thresholds(fs, calib):
    """Порог qNN каждого q-терма по объединению суток fs; пусто → nan (условие ложно). calib — порог по калибровочному отрезку."""
    out = {"_calib": float(calib)}
    for k, (p, _, _) in CFG["_qf"].items():
        a = np.concatenate([f[k] for f in fs]) if fs else np.empty(0)
        out["_" + k] = float(np.quantile(a, p / 100.0)) if a.size else float("nan")
    return out


def evaluate(o, thr):
    """Один файл: все гипотезы → (out: [(гипотеза, сутки, вектор)], hist). thr — пороги q-термов монеты на этот файл."""
    if o is None:
        return None, None
    env, days, n_rows = o
    if n_rows == 0:
        return [], []
    env.update(thr)
    vals = np.stack([env[c] for c in VAL], axis=1)
    ok = ~np.isnan(vals)
    z = np.where(ok, vals, 0.0)
    out, hist = [], []
    ud, inv = np.unique(days, return_inverse=True)
    for hi, (hid, f, _, sign, qs) in enumerate(CFG["_hyp"]):
        mask = np.asarray(f(env), dtype=bool)
        if mask.shape == ():
            mask = np.full(n_rows, bool(mask))
        if not mask.any():
            continue
        zz, oo = z[mask], ok[mask]
        if sign < 0:  # против отскока: markout со знаком, favour ↔ adverse
            zz = zz.copy()
            zz[:, :len(MH)] *= -1
            zz[:, len(MH):] = np.concatenate([zz[:, len(MH) + 4:], zz[:, len(MH):len(MH) + 4]], axis=1)
            oo = np.concatenate([oo[:, :len(MH)], oo[:, len(MH) + 4:], oo[:, len(MH):len(MH) + 4]], axis=1)
        for j, hc in enumerate(HI):
            x = zz[oo[:, hc], hc]
            if x.size:
                k = np.minimum(np.ceil(np.arcsinh(np.abs(x)) / STEP), KM).astype(np.int64)
                u, c = np.unique(KM + np.where(x < 0, -k, k) + j * NB, return_counts=True)
                hist.append((hi, u, c))
        dm = inv[mask]
        calib = thr.get("_calib", 0.0) if qs else 0.0  # событий с порогом по калибровочному отрезку (первые 7 суток монеты)
        for k in np.unique(dm):
            s = dm == k
            out.append((hi, str(ud[k]), np.concatenate([[s.sum()], zz[s].sum(axis=0), oo[s].sum(axis=0), [calib * s.sum()]])))
    return out, hist


def work(a):
    """Задача = монета в одном рынке и популяции: файлы по суткам в порядке дат. q-порог монеты на сутки d — по суткам
    [d−7, d−1] (причинно); первые 7 суток монеты (d < первая + 7) — по калибровочному отрезку [первая, первая + 6]
    (в том числе по себе), число таких событий — в клетке (В-211). → [(месяц, путь, out, hist)] по файлам emit."""
    pop, src, sym, files = a
    res = []
    if not CFG["_qk"]:
        for dt, mon, p, emit in files:
            if emit:
                res.append((mon, p, *evaluate(load(src, mon, sym, p), {})))
        return a, res
    day = [int(np.datetime64(f[0], "D").astype(np.int64)) for f in files]
    first = day[0]
    emits = [i for i, f in enumerate(files) if f[3]]
    need = set()
    for i in emits:
        lo = first if day[i] < first + 7 else day[i] - 7
        hi = first + 6 if day[i] < first + 7 else day[i]
        need |= {j for j in range(len(files)) if lo <= day[j] <= hi}
    fs = {}  # индекс файла → q-признаки (сутки в окне)
    cal = [j for j in range(len(files)) if day[j] < first + 7 and j in need]
    keep = any(files[j][3] for j in cal)
    held = {}
    for j in cal:
        o = load(src, files[j][1], sym, files[j][2])
        fs[j] = feats(o)
        if keep:
            held[j] = o
    if keep:
        thr = thresholds([fs[j] for j in cal], 1)
        for j in cal:
            if files[j][3]:
                res.append((files[j][1], files[j][2], *evaluate(held[j], dict(thr))))
    held = None
    for i, (dt, mon, p, emit) in enumerate(files):
        if day[i] < first + 7 or i not in need:
            continue
        o = load(src, mon, sym, p)
        fs[i] = feats(o)
        if emit:
            win = [fs[j] for j in fs if day[i] - 7 <= day[j] < day[i]]
            res.append((mon, p, *evaluate(o, thresholds(win, 0))))
        for j in [j for j in fs if day[j] < day[i] - 7]:
            del fs[j]
    return a, res


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


def quant(h, qs):
    """Квантили по гистограмме хода (корзины asinh): середина корзины в бп."""
    n = h.sum()
    cum = np.cumsum(h)
    out = []
    for q in qs:
        k = int(np.searchsorted(cum, q * n)) - KM
        out.append(np.sign(k) * np.sinh((abs(k) - 0.5) * STEP) if k else 0.0)
    return out


def cell(a, key, B, seed, H):
    """Итог клетки из сумм по суткам и по монетам; H — гистограммы хода (len(HD), NB)."""
    tot = sum(a["sym"].values())
    n = tot[0]
    top = sorted(a["sym"].items(), key=lambda kv: -kv[1][0])[:3]
    row = {"n": int(n), "n_days": len(a["day"]), "n_coins": len(a["sym"]), "top3_n_share": sum(v[0] for _, v in top) / n}
    row["n_calib"] = int(tot[-1])  # событий с q-порогом по калибровочному отрезку (первые 7 суток монеты); 0 — нет q-термов или всё причинно
    S = np.divide(tot[1:1 + NV], tot[1 + NV:1 + 2 * NV], out=np.full(NV, np.nan), where=tot[1 + NV:1 + 2 * NV] > 0)
    ex = tot - sum(v for _, v in top)
    Sx = np.divide(ex[1:1 + NV], ex[1 + NV:1 + 2 * NV], out=np.full(NV, np.nan), where=ex[1 + NV:1 + 2 * NV] > 0)
    D = np.array([a["day"][d] for d in sorted(a["day"])])  # по возрастанию даты: бутстрап не зависит от порядка завершения задач
    rng = np.random.default_rng(seed + zlib.crc32("|".join(key).encode()))
    lo = np.full(NV, np.nan)
    hi = np.full(NV, np.nan)
    if len(D) > 1 and B:
        idx = rng.integers(0, len(D), size=(B, len(D)))
        sb = D[idx, 1:1 + NV].sum(axis=1)
        cb = D[idx, 1 + NV:1 + 2 * NV].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            bs = sb / cb
        lo, hi = np.nanpercentile(bs, 2.5, axis=0), np.nanpercentile(bs, 97.5, axis=0)
    for i, c in enumerate(VAL):
        row[f"S_{c}"] = S[i]
        if c in MH:
            row[f"lo_{c}"], row[f"hi_{c}"], row[f"Sex3_{c}"] = lo[i], hi[i], Sx[i]
    row["h_src"] = "tick"  # горизонты — из тикового кэша касаний (не из секундного ряда)
    for j, c in enumerate(HD):
        h, nn = H[j], H[j].sum()
        p10, med, p90 = quant(h, (0.1, 0.5, 0.9)) if nn else (np.nan,) * 3
        row[f"up_{c}"], row[f"dn_{c}"] = (h[KM + 1:].sum() / nn, h[:KM].sum() / nn) if nn else (np.nan, np.nan)
        row[f"p10_{c}"], row[f"med_{c}"], row[f"p90_{c}"] = p10, med, p90
    return row


def jobs_for(cfg, nd):
    """Задачи (pop, src, sym, [(дата, месяц, путь, emit)]) — по монете, файлы по возрастанию даты. emit — месяц в cfg["months"]
    (необязательно: {"fut": ["jul"], "spot": ["sep"]} — гейт малым срезом); файлы вне months нужны только q-порогам (окно 7 суток)."""
    only = cfg.get("months")
    tasks = []
    for pop, spec in cfg["caches"].items():
        for src, g in spec.items():
            by = collections.defaultdict(list)
            for m in (MON if "{m}" in g else [None]):
                for p in glob.glob(g.format(m=m) if m else g):
                    pp = p.replace("\\", "/")
                    mon = m or MON[int(re.search(r"/D20/\d{4}-(\d\d)", pp).group(1)) - 1]  # спот: месяц по D20/2026-MM в пути
                    by[os.path.basename(pp)[len("touches-"):-4]].append((pp.split("/")[-2], mon, p))
            for sym, fl in sorted(by.items()):
                fl.sort()
                if nd:
                    fl = fl[:nd]
                tasks.append((pop, src, sym, [(dt, mon, p, not only or mon in only.get(src, MON)) for dt, mon, p in fl]))
    return tasks


def main():
    cfg = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    nd = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 0
    os.makedirs(out, exist_ok=True)
    init(json.loads(json.dumps(cfg)))  # проверка конфига до прохода; воркеры получают чистый cfg (spawn)
    ids = [h["id"] for h in cfg["hypotheses"]]
    assert len(set(ids)) == len(ids), "id гипотез повторяются"
    jobs = jobs_for(cfg, nd)
    nfiles = sum(f[3] for j in jobs for f in j[3])
    agg, bad, hg, done = {}, 0, {}, 0
    t0 = time.time()
    print(f"tasks {len(jobs)} files {nfiles}", file=sys.stderr, flush=True)
    with mp.Pool(int(os.environ.get("NX", "15")), initializer=init, initargs=(cfg,)) as pool:
        for (pop, src, sym, _), res in pool.imap_unordered(work, jobs, chunksize=1):
            for mon, p, o, hl in res:
                done += 1
                if done % 100 == 0:
                    print(f"done {done}/{nfiles} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
                if o is None:
                    bad += 1
                    continue
                for hi, day, vec in o:
                    merge(agg, (ids[hi], pop, src, mon), day, sym, vec)
                for hi, u, c in hl:
                    np.add.at(hg.setdefault((ids[hi], pop, src, mon), np.zeros(len(HD) * NB, dtype=np.int64)), u, c)
    B, seed = int(cfg.get("boot", 1000)), int(cfg.get("seed", 20261022))
    rows = {k: cell(a, k, B, seed, hg.get(k, np.zeros(len(HD) * NB, dtype=np.int64)).reshape(len(HD), NB)) for k, a in agg.items()}
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
    json.dump({"jobs": nfiles, "bad_header_files": bad, "hypotheses": len(ids), "cells": len(rows),
               "hashes_hyp_x_pop_x_src_x_month": len(rows), "hyp_x_pop_x_src": hashes}, open(os.path.join(out, "pseudotest-summary.json"), "w"))
    open(os.path.join(out, "pseudotest.done"), "w").write("ok\n")


if __name__ == "__main__":
    main()
