#!/usr/bin/env python3
"""TK-493, П-39 (аудит материалов владельца 07.11, предложение T1): ПЕРЕПИСЬ ХВОСТОВ РАСХОЖДЕНИЙ крипты между 8 минутными биржами.
ОПИСАТЕЛЬНО: торгового правила и статусов нет. Решает, запускать ли семейство L П-39 («потолок входа выше»): условие - >= 30 единиц «актив x сутки» с |dev| >= ecap (ecap 1000, 2000).

Определения (задача T1; R2' как П-38 §4.1, функции conv_lib без изменений):
  s_t = 1e4*ln(cA/cB) на минутах, где v > 0 у обеих ног (в панелях .sap v = 0 и дыры = NaN, поэтому «обе ноги торговались» = оба close конечны);
  b_t = медиана s за предыдущие 1440 минут (пересчёт на минутах, кратных 5, от начала ряда 01.10 00:00; нужно >= 240 наблюдений) = conv_lib.basis_sigma(s, 1440, 5, 240, None);
  dev = s - b. Эпизод = серия ПОДРЯД идущих календарных минут с |dev| >= theta одного знака (как conv_hints.run_lengths); минута без сделки у любой ноги (NaN) обрывает серию.
  Сутки 2465..2499 от 2020-01-01 (01.10..04.11.2026): формирование 2465..2478, окно 2479..2499. Одна непрерывная серия (база окна считается по прошлому, в т.ч. по формированию).
  Период эпизода - по минуте начала. Единица «актив x сутки» - пара (key актива, сутки), в которых есть минута с |dev| >= theta хотя бы у одной пары бирж этого актива.
  «Протухшая нога» (помечается): число подряд идущих минут без сделки НА НОГЕ непосредственно перед началом эпизода (gA, gB); stale = max(gA, gB) >= 1,
  т.е. отклонение появилось на первой сделке после пустой минуты (цена ноги могла не обновляться). Внутри эпизода пустых минут нет по построению.
  Сходимость: |dev| <= theta/4 ровно на минуте t0+h (h = 1, 5, 30, 120; знаменатель - эпизоды, у которых на этой минуте есть наблюдение), и «первое наблюдение |dev| <= theta/4 не позже t0+h» (fp).
Читает ТОЛЬКО ~/xdl9/panels/*/*.sap (close) и ~/xdl9/asset_pairs.csv; ~/xdl9/c2x, conv_window.py, conv_guard.py, c2/window, c2/out*, c2g/out* не трогает. Журнал чтений - <out>/reads.log.
Запуск: census_tails.py run --root ~/xdl9 --out ~/xdl9/census/out --threads 3 [--limit N];  census_tails.py test
Только numpy и stdlib.
"""
import argparse
import csv
import datetime
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_lib as L      # noqa: E402  (копия conv_lib.py из коммита 80bf9fc2, без изменений)

MIN_DAY = 1440
D0, D1 = 2465, 2499
D_FORM_LAST = 2478
NM = (D1 - D0 + 1) * MIN_DAY                # 50 400 минут
SPLIT = (D_FORM_LAST + 1 - D0) * MIN_DAY    # 20 160: граница формирование / окно
NDAYS = D1 - D0 + 1                         # 35
THETAS = (160, 320, 640, 1000, 2000)        # 2000 - только для ворот L (ecap)
CONV_H = (1, 5, 30, 120)
BASE_L, BASE_STEP, BASE_NB = 1440, 5, 240
FORM_MASK = (1 << (D_FORM_LAST + 1 - D0)) - 1
ALL_MASK = (1 << NDAYS) - 1
WIN_MASK = ALL_MASK & ~FORM_MASK
EX8 = ("binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter")
EP_DTYPE = np.dtype([("pair", "i4"), ("t0", "i4"), ("D", "i4"), ("sign", "i1"), ("mx", "f4"), ("gA", "i4"), ("gB", "i4"), ("end", "i1"),
                     ("d1", "f4"), ("d5", "f4"), ("d30", "f4"), ("d120", "f4"), ("fp", "i4")])
END_NAMES = {0: "конец данных", 1: "ниже порога", 2: "обратный знак", 3: "нет сделки на ноге"}
# гистограмма |dev|: шаг 0.5 до 100, 5 до 1000, 50 до 20000 bps; последний ящик - всё выше
HIST_EDGES = np.concatenate([np.arange(0, 100, 0.5), np.arange(100, 1000, 5.0), np.arange(1000, 20000 + 1, 50.0)])


# ------------------------------------------------------------------ чтение панели
def load_close(path):
    """close панели .sap за сутки D0..D1 (NaN вне спана и там, где v = 0 или дыра). Формат: 'SAP1', u32, i64 first, u64 n, затем n open и n close (f8 LE)."""
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first = struct.unpack("<q", h[8:16])[0]
    n = struct.unpack("<Q", h[16:24])[0]
    if os.path.getsize(path) != 24 + 16 * n:
        raise ValueError("размер файла не 24 + 16n: " + path)
    m0, m1 = D0 * MIN_DAY, (D1 + 1) * MIN_DAY
    c = np.full(m1 - m0, np.nan)
    lo, hi = max(m0, first), min(m1, first + n)
    if hi > lo:
        c[lo - m0:hi - m0] = np.fromfile(path, dtype="<f8", count=hi - lo, offset=24 + n * 8 + (lo - first) * 8)
    return c


def pair_series(cA, cB):
    """-> s, dev, ok (dev конечно), vA, vB (нога торговалась). База - conv_lib.basis_sigma(1440, 5, 240)."""
    s, valid = L.spread_series(cA, cB)
    b, _, _ = L.basis_sigma(s, BASE_L, BASE_STEP, BASE_NB, None)
    dev = s - b
    ok = np.isfinite(dev)
    vA = np.isfinite(cA) & (cA > 0)
    vB = np.isfinite(cB) & (cB > 0)
    return s, dev, ok, vA, vB


# ------------------------------------------------------------------ эпизоды
def runs(dev, theta):
    """Серии подряд идущих календарных минут с |dev| >= theta и неизменным знаком dev. -> (начала, длины). Та же логика, что conv_hints.run_lengths."""
    ok = np.isfinite(dev)
    code = np.zeros(len(dev), dtype=np.int8)
    with np.errstate(invalid="ignore"):
        hit = ok & (np.abs(dev) >= theta)
    code[hit] = np.sign(dev[hit]).astype(np.int8)
    cp = np.concatenate(([0], code, [0])).astype(np.int8)
    b = np.flatnonzero(cp[1:] != cp[:-1])
    st, en = b[:-1], b[1:]
    st = st[st < len(dev)]
    en = en[:len(st)]
    m = code[st] != 0
    return st[m], (en - st)[m]


def episodes_for(theta, dev, ok, vA, vB, pair_id):
    """Таблица эпизодов (EP_DTYPE) одной пары при пороге theta; пустой массив, если эпизодов нет."""
    N = len(dev)
    st, dur = runs(dev, theta)
    n = len(st)
    E = np.zeros(n, dtype=EP_DTYPE)
    if n == 0:
        return E
    en = st + dur
    absd = np.where(ok, np.abs(dev), 0.0)
    ad_ext = np.append(absd, 0.0)
    idx = np.empty(2 * n, dtype=np.int64)
    idx[0::2] = st
    idx[1::2] = en
    E["pair"] = pair_id
    E["t0"] = st
    E["D"] = dur
    E["sign"] = np.sign(dev[st]).astype(np.int8)
    E["mx"] = np.maximum.reduceat(ad_ext, idx)[0::2]
    # протухшая нога: подряд пустых минут, заканчивающихся на t0-1
    t1 = np.maximum(st - 1, 0)
    for col, v in (("gA", vA), ("gB", vB)):
        li = L.ffill_idx(v)
        lj = li[t1]
        E[col] = np.where(lj >= 0, t1 - lj, 32000)
    # что после эпизода
    inside = en < N
    de = np.where(inside, dev[np.minimum(en, N - 1)], np.nan)
    typ = np.zeros(n, dtype=np.int8)
    typ[inside & ~np.isfinite(de)] = 3
    fin = inside & np.isfinite(de)
    with np.errstate(invalid="ignore"):
        typ[fin & (np.abs(de) < theta)] = 1
        typ[fin & (np.abs(de) >= theta)] = 2
    E["end"] = typ
    # значение dev на t0+h
    for h in CONV_H:
        j = st + h
        dh = np.full(n, np.nan, dtype=np.float32)
        m = j < N
        dh[m] = dev[j[m]]
        E["d%d" % h] = dh
    # первое наблюдение |dev| <= theta/4 после начала
    with np.errstate(invalid="ignore"):
        q = np.flatnonzero(ok & (np.abs(np.where(ok, dev, 0.0)) <= theta / 4.0))
    if len(q):
        pos = np.searchsorted(q, st + 1, side="left")
        got = pos < len(q)
        E["fp"] = np.where(got, q[np.minimum(pos, len(q) - 1)] - st, -1)
    else:
        E["fp"] = -1
    return E


# ------------------------------------------------------------------ счёт одной пары и куска пар
def process_pair(task):
    pid, p1, p2 = task[:3]
    cA, cB = load_close(p1), load_close(p2)
    s, dev, ok, vA, vB = pair_series(cA, cB)
    nv = np.array([int(ok[:SPLIT].sum()), int(ok[SPLIT:].sum())], dtype=np.int64)
    stat = {"pid": pid, "nv": nv, "mx": np.zeros(2), "hit": np.zeros((len(THETAS), 2), dtype=np.int64), "low": np.zeros((len(THETAS), 2), dtype=np.int64)}
    hist = np.zeros((2, len(HIST_EDGES)), dtype=np.int64)
    eps = [np.zeros(0, dtype=EP_DTYPE) for _ in THETAS]
    if nv.sum() == 0:
        return stat, eps, hist
    absd = np.abs(dev)
    for per, sl in ((0, slice(0, SPLIT)), (1, slice(SPLIT, NM))):
        a = absd[sl][ok[sl]]
        if len(a):
            stat["mx"][per] = a.max()
            hist[per] = np.bincount(np.searchsorted(HIST_EDGES, a, side="right") - 1, minlength=len(HIST_EDGES))
    for i, th in enumerate(THETAS):
        with np.errstate(invalid="ignore"):
            hit = ok & (absd >= th)
        stat["hit"][i] = (int(hit[:SPLIT].sum()), int(hit[SPLIT:].sum()))
        with np.errstate(invalid="ignore"):
            low = ok & (absd <= th / 4.0)
        stat["low"][i] = (int(low[:SPLIT].sum()), int(low[SPLIT:].sum()))      # фон: пар-минут с |dev| <= theta/4
        eps[i] = episodes_for(th, dev, ok, vA, vB, pid)
    return stat, eps, hist


def _init_worker():
    try:
        os.nice(10)
    except OSError:
        pass


def worker_chunk(tasks):
    stats, hist = [], np.zeros((2, len(HIST_EDGES)), dtype=np.int64)
    eps = [[] for _ in THETAS]
    for t in tasks:
        st, ep, h = process_pair(t)
        stats.append(st)
        hist += h
        for i in range(len(THETAS)):
            if len(ep[i]):
                eps[i].append(ep[i])
    return stats, [np.concatenate(e) if e else np.zeros(0, dtype=EP_DTYPE) for e in eps], hist


# ------------------------------------------------------------------ единицы «актив x сутки»
def popcount(a):
    a = np.atleast_1d(np.asarray(a, dtype=np.int64)).astype("<u8")
    if a.size == 0:
        return np.zeros(0, dtype=np.int64)
    return np.unpackbits(a.view(np.uint8)).reshape(-1, 64).sum(axis=1)


def day_mask(E):
    """Битовая маска суток (бит 0 = сутки D0), которые покрывают минуты эпизода."""
    ds = E["t0"].astype(np.int64) // MIN_DAY
    de = (E["t0"].astype(np.int64) + E["D"] - 1) // MIN_DAY
    return (np.int64(1) << (de + 1)) - (np.int64(1) << ds)


def asset_masks(E, asset_of_pair):
    """-> (asset_ids, any, live, stale, live2): маски суток по активам; live - эпизоды с gA = gB = 0, stale - прочие, live2 - живые эпизоды длиной >= 2 минут."""
    if len(E) == 0:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z, z, z
    asset = asset_of_pair[E["pair"]]
    m = day_mask(E)
    live = np.maximum(E["gA"], E["gB"]) == 0
    order = np.argsort(asset, kind="stable")
    ua, st = np.unique(asset[order], return_index=True)

    def red(x):
        return np.bitwise_or.reduceat(x[order], st)
    zero = np.int64(0)
    return (ua, red(m), red(np.where(live, m, zero)), red(np.where(~live, m, zero)), red(np.where(live & (E["D"] >= 2), m, zero)))


def spells(masks):
    """Число серий подряд идущих суток по активам (соседние сутки одного актива = одна серия)."""
    m = np.asarray(masks, dtype=np.int64)
    return int(popcount(m & ~(m << 1)).sum())


def units_in(masks, per_mask):
    return int(popcount(np.asarray(masks, dtype=np.int64) & np.int64(per_mask)).sum()) if len(masks) else 0


# ------------------------------------------------------------------ сводка по подмножеству эпизодов
SUM_COLS = ["episodes", "minutes", "units", "units_live", "units_stale_only", "assets", "asset_pairs", "ex_pairs", "mx_max", "mx_med",
            "D_med", "D_q90", "D_le1", "D_le2", "D_le5", "D_le30",
            "obs1", "conv1", "obs5", "conv5", "obs30", "conv30", "obs120", "conv120",
            "ever1", "ever5", "ever30", "ever120", "den1", "den5", "den30", "den120",
            "stale_any", "stale_ge5", "stale_ge30", "end_below", "end_flip", "end_noobs", "end_data"]


def summarize(E, theta, asset_of_pair, expair_of_pair, per_mask):
    """Строка сводки: E - эпизоды ОДНОГО порога theta (уже отфильтрованные по периоду/срезу); per_mask - маска суток периода."""
    r = {c: 0 for c in SUM_COLS}
    n = len(E)
    r["episodes"] = n
    if n == 0:
        return r
    D = E["D"]
    r["minutes"] = int(D.sum())
    ua, am, al, ast, _ = asset_masks(E, asset_of_pair)
    r["units"] = units_in(am, per_mask)
    r["units_live"] = units_in(al, per_mask)
    r["units_stale_only"] = r["units"] - r["units_live"]
    r["assets"] = int(len(ua))
    r["asset_pairs"] = int(len(np.unique(E["pair"])))
    r["ex_pairs"] = int(len(np.unique(expair_of_pair[E["pair"]])))
    r["mx_max"] = float(E["mx"].max())
    r["mx_med"] = float(np.median(E["mx"]))
    r["D_med"] = float(np.median(D))
    r["D_q90"] = float(np.quantile(D, 0.9))
    for k in (1, 2, 5, 30):
        r["D_le%d" % k] = int((D <= k).sum())
    thr = theta / 4.0
    for h in CONV_H:
        d = E["d%d" % h]
        obs = np.isfinite(d)
        r["obs%d" % h] = int(obs.sum())
        with np.errstate(invalid="ignore"):
            r["conv%d" % h] = int((obs & (np.abs(d) <= thr)).sum())
        cens = (E["t0"].astype(np.int64) + h) <= NM - 1          # у эпизода есть хотя бы теоретическая возможность наблюдения на t0+h
        r["den%d" % h] = int(cens.sum())
        r["ever%d" % h] = int((cens & (E["fp"] >= 1) & (E["fp"] <= h)).sum())
    g = np.maximum(E["gA"], E["gB"])
    r["stale_any"] = int((g >= 1).sum())
    r["stale_ge5"] = int((g >= 5).sum())
    r["stale_ge30"] = int((g >= 30).sum())
    r["end_below"] = int((E["end"] == 1).sum())
    r["end_flip"] = int((E["end"] == 2).sum())
    r["end_noobs"] = int((E["end"] == 3).sum())
    r["end_data"] = int((E["end"] == 0).sum())
    return r


# ------------------------------------------------------------------ события (склейка эпизодов разных пар одного актива) и крупнейшие
def top_events(E, meta, n_top):
    """Крупнейшие события = единицы «актив x сутки» (сутки начала эпизода) по max|dev|: представитель - эпизод с наибольшим max|dev| этих суток актива;
    n_ep / n_pairs / биржи - по ВСЕМ эпизодам актива в эти сутки; общие для всех пар события биржи = выбросная нога. -> список dict по убыванию."""
    if len(E) == 0:
        return []
    asset = meta["asset"][E["pair"]].astype(np.int64)
    key = asset * 64 + E["t0"].astype(np.int64) // MIN_DAY
    order = np.lexsort((-E["mx"], key))
    uk, first = np.unique(key[order], return_index=True)
    reps = order[first]
    reps = reps[np.argsort(-E["mx"][reps], kind="stable")][:n_top]
    out = []
    for r in reps:
        sub = E[key == key[r]]
        exs = [set((meta["ex1"][p], meta["ex2"][p])) for p in sub["pair"]]
        out.append({"rep": E[r], "n_ep": len(sub), "n_pairs": len(set(int(p) for p in sub["pair"])),
                    "exchanges": sorted(set().union(*exs)), "common": sorted(set.intersection(*exs)),
                    "n_ge320": int((sub["mx"] >= 320).sum()), "n_ge1000": int((sub["mx"] >= 1000).sum())})
    return out


def event_detail(ev, meta, root, theta):
    """Дочитывает панели пары-представителя: пик, ход каждой ноги от своей предыдущей сделки, исход. -> dict для строки таблицы."""
    rep = ev["rep"]
    pid = int(rep["pair"])
    cA = load_close(meta["path1"][pid])
    cB = load_close(meta["path2"][pid])
    s, dev, ok, vA, vB = pair_series(cA, cB)
    t0, D = int(rep["t0"]), int(rep["D"])
    seg = np.where(ok[t0:t0 + D], np.abs(dev[t0:t0 + D]), -1.0)
    pk = t0 + int(np.argmax(seg))
    liA, liB = L.ffill_idx(vA), L.ffill_idx(vB)
    ra, rb = liA[t0 - 1], liB[t0 - 1]
    movA = 1e4 * float(np.log(cA[pk] / cA[ra])) if ra >= 0 else float("nan")
    movB = 1e4 * float(np.log(cB[pk] / cB[rb])) if rb >= 0 else float("nan")
    dd = datetime.date(2020, 1, 1) + datetime.timedelta(days=D0 + t0 // MIN_DAY)
    hh, mm = divmod(t0 % MIN_DAY, 60)
    return dict(asset=meta["key"][pid], ex_a=meta["ex1"][pid], ex_b=meta["ex2"][pid], date=dd.isoformat(), t_utc="%02d:%02d" % (hh, mm),
                period="формирование" if t0 < SPLIT else "окно", sign=int(rep["sign"]), mx=float(rep["mx"]), D=D,
                peak_off=pk - t0, mov_a=movA, mov_b=movB, gap_a=int(rep["gA"]), gap_b=int(rep["gB"]),
                d1=float(rep["d1"]), d5=float(rep["d5"]), d30=float(rep["d30"]), d120=float(rep["d120"]), fp=int(rep["fp"]),
                end=END_NAMES[int(rep["end"])], n_pairs=ev["n_pairs"], n_ep=ev["n_ep"], n_ge320=ev["n_ge320"], n_ge1000=ev["n_ge1000"], exchanges="+".join(ev["exchanges"]),
                mover=(meta["ex1"][pid] if abs(movA) >= abs(movB) else meta["ex2"][pid]) if np.isfinite(movA) and np.isfinite(movB) else "",
                common_leg=("одна пара" if ev["n_pairs"] == 1 else ("+".join(ev["common"]) if len(ev["common"]) == 1 else ("нет общей" if not ev["common"] else "неоднозначно: " + "+".join(ev["common"])))))


# ------------------------------------------------------------------ универсум и главный прогон
def load_universe(root):
    path = os.path.join(root, "asset_pairs.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    base = [r for r in rows if r["scope"] == "между биржами" and r["big1"] == "крипта" and r["big2"] == "крипта" and r["class_match"] == "1"]
    prim = [r for r in base if r["c1d2"] == "1"]
    ext = [r for r in base if r["c1d2"] != "1" and r["role1"] == "main" and r["role2"] == "main"]
    return prim, ext, hashlib.sha256(open(path, "rb").read()).hexdigest()


def build_meta(rows, univ_name, root, first_pid):
    meta = {k: [] for k in ("key", "ex1", "ex2", "sym1", "sym2", "path1", "path2", "univ", "verdict", "k", "role")}
    for r in rows:
        meta["key"].append(r["key"])
        meta["ex1"].append(r["ex1"])
        meta["ex2"].append(r["ex2"])
        meta["sym1"].append(r["sym1"])
        meta["sym2"].append(r["sym2"])
        meta["path1"].append(os.path.join(root, "panels", r["ex1"], r["sym1"] + ".sap"))
        meta["path2"].append(os.path.join(root, "panels", r["ex2"], r["sym2"] + ".sap"))
        meta["univ"].append(univ_name)
        meta["verdict"].append(r["verdict"])
        meta["k"].append(r["k"])
    return meta


def log_read(out, event, extra=""):
    os.makedirs(out, exist_ok=True)
    sha = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()[:12]
    with open(os.path.join(out, "reads.log"), "a", encoding="utf-8") as f:
        f.write("%s\tcensus_tails.py\t%s\tpid=%d\t%s\tсутки %d..%d\t%s\n" % (datetime.datetime.now(datetime.timezone.utc).strftime("%FT%TZ"), sha, os.getpid(), event, D0, D1, extra))


def write_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.6g" % r[c]) if isinstance(r.get(c), float) else r.get(c, "") for c in cols])


def run(args):
    root, out = os.path.expanduser(args.root), os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)
    t_start = time.time()
    prim, ext, sha_pairs = load_universe(root)
    univs = []
    if "c1d2" in args.univ:
        univs.append(("c1d2", prim))
    if "ext" in args.univ:
        univs.append(("ext", ext))
    rows_all, tags = [], []
    for name, rows in univs:
        if args.limit and len(rows) > args.limit:
            step = len(rows) / float(args.limit)
            rows = [rows[int(i * step)] for i in range(args.limit)]
        rows_all += rows
        tags += [name] * len(rows)
    # проверка: уникальность и наличие панелей
    seen = set()
    keep, tag_keep, missing = [], [], 0
    for r, t in zip(rows_all, tags):
        k = (r["ex1"], r["sym1"], r["ex2"], r["sym2"])
        if k in seen:
            continue
        seen.add(k)
        if not (os.path.isfile(os.path.join(root, "panels", r["ex1"], r["sym1"] + ".sap")) and os.path.isfile(os.path.join(root, "panels", r["ex2"], r["sym2"] + ".sap"))):
            missing += 1
            continue
        keep.append(r)
        tag_keep.append(t)
    meta = build_meta(keep, "", root, 0)
    meta["univ"] = tag_keep
    NP = len(keep)
    assets = sorted(set(meta["key"]))
    a_idx = {a: i for i, a in enumerate(assets)}
    meta["asset"] = np.array([a_idx[k] for k in meta["key"]], dtype=np.int64)
    exps = sorted(set(tuple(sorted((a, b))) for a, b in zip(meta["ex1"], meta["ex2"])))
    e_idx = {e: i for i, e in enumerate(exps)}
    meta["expair"] = np.array([e_idx[tuple(sorted((a, b)))] for a, b in zip(meta["ex1"], meta["ex2"])], dtype=np.int64)
    meta["univ_arr"] = np.array([0 if t == "c1d2" else 1 for t in tag_keep], dtype=np.int8)
    print("пар: %d (c1d2 %d, ext %d), без панели %d, активов %d, пар бирж %d" % (NP, int((meta["univ_arr"] == 0).sum()), int((meta["univ_arr"] == 1).sum()), missing, len(assets), len(exps)), flush=True)
    log_read(out, "start", "пар %d; asset_pairs.csv sha %s" % (NP, sha_pairs[:12]))

    tasks = [(i, meta["path1"][i], meta["path2"][i], int(meta["univ_arr"][i])) for i in range(NP)]
    chunks = [tasks[i:i + 40] for i in range(0, NP, 40)]
    stats, eps_parts, hist = [], [[] for _ in THETAS], np.zeros((2, len(HIST_EDGES)), dtype=np.int64)
    done = 0
    with mp.get_context("fork").Pool(args.threads, initializer=_init_worker) as pool:
        for st, ep, h in pool.imap_unordered(worker_chunk, chunks):
            stats += st
            hist += h
            for i in range(len(THETAS)):
                if len(ep[i]):
                    eps_parts[i].append(ep[i])
            done += len(st)
            if done // 40 % 10 == 0 or done == NP:
                print("  %d / %d пар, %.0f с" % (done, NP, time.time() - t_start), flush=True)
    EP = []
    for i in range(len(THETAS)):
        E = np.concatenate(eps_parts[i]) if eps_parts[i] else np.zeros(0, dtype=EP_DTYPE)
        E = E[np.lexsort((E["t0"], E["pair"]))]
        EP.append(E)
        print("theta %d: эпизодов %d" % (THETAS[i], len(E)), flush=True)
    stats.sort(key=lambda d: d["pid"])
    log_read(out, "done", "эпизодов по theta %s; %.0f с" % ([len(e) for e in EP], time.time() - t_start))
    np.savez_compressed(os.path.join(out, "episodes.npz"), **{"th%d" % th: EP[i] for i, th in enumerate(THETAS)})
    json.dump({"assets": assets, "expairs": ["+".join(e) for e in exps]}, open(os.path.join(out, "index.json"), "w", encoding="utf-8"), ensure_ascii=False)
    tables(args, root, out, meta, assets, exps, stats, hist, EP, sha_pairs, t_start, missing)
    return 0


def tables(args, root, out, meta, assets, exps, stats, hist, EP, sha_pairs, t_start, missing):
    tdir = os.path.expanduser(args.tables)
    os.makedirs(tdir, exist_ok=True)
    NP = len(meta["key"])
    asset_of_pair, expair_of_pair, univ_arr = meta["asset"], meta["expair"], meta["univ_arr"]
    ex_has = {x: np.array([(a == x or b == x) for a, b in zip(meta["ex1"], meta["ex2"])]) for x in EX8}
    periods = (("формирование", 0, SPLIT, FORM_MASK), ("окно", SPLIT, NM, WIN_MASK), ("всего", 0, NM, ALL_MASK))

    def sel(E, univ, t_lo, t_hi, mask=None):
        m = (univ_arr[E["pair"]] == univ) & (E["t0"] >= t_lo) & (E["t0"] < t_hi)
        if mask is not None:
            m &= mask[E["pair"]]
        return E[m]

    # 1. сводка по порогам
    rows = []
    for univ, uname in ((0, "c1d2"), (1, "ext")):
        for plabel, lo, hi, pm in periods:
            for i, th in enumerate(THETAS):
                r = summarize(sel(EP[i], univ, lo, hi), th, asset_of_pair, expair_of_pair, pm)
                r.update(univ=uname, period=plabel, theta=th)
                rows.append(r)
    cols = ["univ", "period", "theta"] + SUM_COLS
    write_csv(os.path.join(tdir, "t1_summary_theta.csv"), rows, cols)

    # 2. срезы: пара бирж, наличие ноги Bybit / BingX / Aster (универсум c1d2)
    srows = []
    for plabel, lo, hi, pm in periods[:2]:
        for i, th in enumerate(THETAS[:4]):
            E0 = sel(EP[i], 0, lo, hi)
            for j, e in enumerate(exps):
                r = summarize(E0[expair_of_pair[E0["pair"]] == j], th, asset_of_pair, expair_of_pair, pm)
                r.update(slice="пара " + "+".join(e), kind="ex_pair", period=plabel, theta=th, n_asset_pairs=int(((expair_of_pair == j) & (univ_arr == 0)).sum()))
                srows.append(r)
            for x in ("bybit", "bingx", "aster"):
                for flag, lab in ((True, "с ногой "), (False, "без ноги ")):
                    msk = ex_has[x] == flag
                    r = summarize(E0[msk[E0["pair"]]], th, asset_of_pair, expair_of_pair, pm)
                    r.update(slice=lab + x, kind="leg", period=plabel, theta=th, n_asset_pairs=int(((msk) & (univ_arr == 0)).sum()))
                    srows.append(r)
    write_csv(os.path.join(tdir, "t1_slices.csv"), srows, ["kind", "slice", "period", "theta", "n_asset_pairs"] + SUM_COLS)

    # 3. ворота L: единицы «актив x сутки» при ecap 1000 и 2000 (универсум c1d2, затем ext справочно)
    grows = []
    unit_rows = []
    for univ, uname in ((0, "c1d2"), (1, "ext")):
        for ecap in (1000, 2000):
            i = THETAS.index(ecap)
            E0 = sel(EP[i], univ, 0, NM)
            ua, am, al, ast, al2 = asset_masks(E0, asset_of_pair)
            row = dict(univ=uname, ecap=ecap, episodes=len(E0), minutes=int(E0["D"].sum()) if len(E0) else 0, assets=int(len(ua)),
                       asset_pairs=int(len(np.unique(E0["pair"]))) if len(E0) else 0)
            for plabel, pm in (("form", FORM_MASK), ("win", WIN_MASK), ("all", ALL_MASK)):
                row["units_" + plabel] = units_in(am, pm)
                row["units_live_" + plabel] = units_in(al, pm)
                row["units_stale_only_" + plabel] = units_in(am, pm) - units_in(al, pm)
                row["units_live_D2_" + plabel] = units_in(al2, pm)
            per_asset = popcount(am) if len(am) else np.zeros(0, dtype=np.int64)
            row["spells_all"] = spells(am)
            row["assets_live"] = int((al != 0).sum())
            row["max_units_one_asset"] = int(per_asset.max()) if len(per_asset) else 0
            row["top_asset"] = assets[int(ua[int(np.argmax(per_asset))])] if len(per_asset) else ""
            row["units_wo_top_asset"] = int(per_asset.sum() - per_asset.max()) if len(per_asset) else 0
            row["days_distinct"] = int(popcount(np.bitwise_or.reduce(am))[0]) if len(am) else 0
            row["gate_ge30"] = "да" if row["units_all"] >= 30 else "нет"
            grows.append(row)
            if univ == 0 and len(E0):
                # список самих единиц: (актив, сутки), покрытые минутами эпизодов
                agg = {}
                for e in E0:
                    ds, de = int(e["t0"]) // MIN_DAY, (int(e["t0"]) + int(e["D"]) - 1) // MIN_DAY
                    a = int(asset_of_pair[int(e["pair"])])
                    for d in range(ds, de + 1):
                        agg.setdefault((a, d), []).append(e)
                for (a, d), lst in sorted(agg.items()):
                    mxs = [float(x["mx"]) for x in lst]
                    best = lst[int(np.argmax(mxs))]
                    pid = int(best["pair"])
                    unit_rows.append(dict(ecap=ecap, asset=assets[a], date=(datetime.date(2020, 1, 1) + datetime.timedelta(days=D0 + d)).isoformat(),
                                          period="формирование" if d <= D_FORM_LAST - D0 else "окно", episodes=len(lst), pairs=len(set(int(x["pair"]) for x in lst)),
                                          mx_max=max(mxs), D_max=max(int(x["D"]) for x in lst), live_episodes=sum(1 for x in lst if max(int(x["gA"]), int(x["gB"])) == 0),
                                          stale_episodes=sum(1 for x in lst if max(int(x["gA"]), int(x["gB"])) >= 1),
                                          best_pair="%s:%s+%s:%s" % (meta["ex1"][pid], meta["sym1"][pid], meta["ex2"][pid], meta["sym2"][pid])))
    gcols = ["univ", "ecap", "episodes", "minutes", "assets", "asset_pairs", "units_form", "units_win", "units_all", "units_live_form", "units_live_win", "units_live_all",
             "units_stale_only_form", "units_stale_only_win", "units_stale_only_all", "units_live_D2_form", "units_live_D2_win", "units_live_D2_all",
             "spells_all", "assets_live", "max_units_one_asset", "top_asset", "units_wo_top_asset", "days_distinct", "gate_ge30"]
    write_csv(os.path.join(tdir, "t1_gate_L.csv"), grows, gcols)
    write_csv(os.path.join(tdir, "t1_units_ecap.csv"), unit_rows, ["ecap", "asset", "date", "period", "episodes", "pairs", "mx_max", "D_max", "live_episodes", "stale_episodes", "best_pair"])

    # 4. крупнейшие события (theta = 160, универсум c1d2 и ext)
    i160 = THETAS.index(160)
    for univ, uname, ntop in ((0, "c1d2", 100), (1, "ext", 30)):
        evs = top_events(sel(EP[i160], univ, 0, NM), meta, ntop)
        det = [event_detail(ev, meta, args.root, 160) for ev in evs]
        dcols = ["asset", "ex_a", "ex_b", "date", "t_utc", "period", "sign", "mx", "D", "peak_off", "mov_a", "mov_b", "gap_a", "gap_b", "d1", "d5", "d30", "d120", "fp", "end",
                 "n_pairs", "n_ep", "n_ge320", "n_ge1000", "exchanges", "mover", "common_leg"]
        write_csv(os.path.join(tdir, "t1_top%d_%s.csv" % (ntop, uname)), det, dcols)

    # 5. эпизоды theta >= 1000 (все, если их не больше 20 000)
    for th in (1000,):
        E = EP[THETAS.index(th)]
        if len(E) <= 20000:
            rws = []
            for e in E:
                pid = int(e["pair"])
                dd = datetime.date(2020, 1, 1) + datetime.timedelta(days=D0 + int(e["t0"]) // MIN_DAY)
                hh, mm = divmod(int(e["t0"]) % MIN_DAY, 60)
                rws.append(dict(univ="c1d2" if univ_arr[pid] == 0 else "ext", asset=meta["key"][pid], ex_a=meta["ex1"][pid], ex_b=meta["ex2"][pid], date=dd.isoformat(), t_utc="%02d:%02d" % (hh, mm),
                                sign=int(e["sign"]), mx=float(e["mx"]), D=int(e["D"]), gap_a=int(e["gA"]), gap_b=int(e["gB"]), end=END_NAMES[int(e["end"])],
                                d1=float(e["d1"]), d5=float(e["d5"]), d30=float(e["d30"]), d120=float(e["d120"]), fp=int(e["fp"])))
            write_csv(os.path.join(tdir, "t1_episodes_theta%d.csv" % th), rws, ["univ", "asset", "ex_a", "ex_b", "date", "t_utc", "sign", "mx", "D", "gap_a", "gap_b", "end", "d1", "d5", "d30", "d120", "fp"])

    # 6. распределение |dev| по ящикам (универсум: все пары вместе)
    qrows = []
    for per, lab in ((0, "формирование"), (1, "окно")):
        h = hist[per]
        cs = np.cumsum(h)
        tot = int(cs[-1])
        row = dict(period=lab, pair_minutes=tot)
        for q in (0.5, 0.9, 0.99, 0.999, 0.9999):
            j = int(np.searchsorted(cs, q * tot, side="left"))
            row["q%s" % ("%g" % q)] = float(HIST_EDGES[min(j, len(HIST_EDGES) - 1)])
        mxs = np.array([s["mx"][per] for s in stats])
        row["max_pair"] = float(mxs.max()) if len(mxs) else 0.0
        qrows.append(row)
    write_csv(os.path.join(tdir, "t1_absdev_quantiles.csv"), qrows, ["period", "pair_minutes", "q0.5", "q0.9", "q0.99", "q0.999", "q0.9999", "max_pair"])

    # 6b. фон: знаменатели и доля пар-минут с |dev| <= theta/4 (по универсумам и периодам)
    brow = []
    for univ, uname in ((0, "c1d2"), (1, "ext")):
        sel_s = [s for s in stats if univ_arr[s["pid"]] == univ]
        for per, plabel in ((0, "формирование"), (1, "окно")):
            nv = sum(int(s["nv"][per]) for s in sel_s)
            for i, th in enumerate(THETAS):
                lo = sum(int(s["low"][i][per]) for s in sel_s)
                hi_ = sum(int(s["hit"][i][per]) for s in sel_s)
                brow.append(dict(univ=uname, period=plabel, theta=th, pairs=len(sel_s), pair_minutes=nv, minutes_ge_theta=hi_, per_million=(1e6 * hi_ / nv) if nv else 0.0,
                                 minutes_le_quarter=lo, share_le_quarter=(lo / nv) if nv else 0.0))
    write_csv(os.path.join(tdir, "t1_baseline.csv"), brow, ["univ", "period", "theta", "pairs", "pair_minutes", "minutes_ge_theta", "per_million", "minutes_le_quarter", "share_le_quarter"])

    # 6c. единицы по суткам (c1d2): сколько активов имеют минуту с |dev| >= theta в эти сутки
    drow = []
    per_day = {}
    for th in (160, 320, 640, 1000):
        E0 = sel(EP[THETAS.index(th)], 0, 0, NM)
        ua, am, _, _, _ = asset_masks(E0, asset_of_pair)
        per_day[th] = [int(sum(1 for m in am if (int(m) >> d) & 1)) for d in range(NDAYS)]
    for d in range(NDAYS):
        drow.append(dict(day=D0 + d, date=(datetime.date(2020, 1, 1) + datetime.timedelta(days=D0 + d)).isoformat(), period="формирование" if D0 + d <= D_FORM_LAST else "окно",
                         u160=per_day[160][d], u320=per_day[320][d], u640=per_day[640][d], u1000=per_day[1000][d]))
    write_csv(os.path.join(tdir, "t1_units_by_day.csv"), drow, ["day", "date", "period", "u160", "u320", "u640", "u1000"])

    # 7. сверка минут: по парам (счёт по минутам) против эпизодов (по минуте начала)
    chk = []
    for i, th in enumerate(THETAS):
        mp_form = sum(int(s["hit"][i][0]) for s in stats)
        mp_win = sum(int(s["hit"][i][1]) for s in stats)
        E = EP[i]
        chk.append(dict(theta=th, minutes_pairs_form=mp_form, minutes_pairs_win=mp_win, minutes_eps_total=int(E["D"].sum()) if len(E) else 0,
                        minutes_pairs_total=mp_form + mp_win))
    write_csv(os.path.join(tdir, "t1_minutes_check.csv"), chk, ["theta", "minutes_pairs_form", "minutes_pairs_win", "minutes_pairs_total", "minutes_eps_total"])

    # 8. таблица пар (Deck, не в git)
    with gzip.open(os.path.join(out, "pairs.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["pid", "univ", "key", "ex1", "sym1", "ex2", "sym2", "verdict", "k", "nv_form", "nv_win", "mx_form", "mx_win"])
        for s in stats:
            p = s["pid"]
            w.writerow([p, meta["univ"][p], meta["key"][p], meta["ex1"][p], meta["sym1"][p], meta["ex2"][p], meta["sym2"][p], meta["verdict"][p], meta["k"][p],
                        int(s["nv"][0]), int(s["nv"][1]), "%.3f" % s["mx"][0], "%.3f" % s["mx"][1]])

    me = dict(code_sha256=hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(), conv_lib_sha256=hashlib.sha256(open(os.path.join(HERE, "conv_lib.py"), "rb").read()).hexdigest(),
              asset_pairs_sha256=sha_pairs, pairs=NP, pairs_c1d2=int((univ_arr == 0).sum()), pairs_ext=int((univ_arr == 1).sum()), missing_panels=missing, assets=len(assets), ex_pairs=len(exps),
              thetas=list(THETAS), days=[D0, D1], form_last=D_FORM_LAST, seconds=round(time.time() - t_start, 1), episodes={str(th): int(len(EP[i])) for i, th in enumerate(THETAS)},
              pair_minutes_valid_dev={"form": int(sum(int(s["nv"][0]) for s in stats)), "win": int(sum(int(s["nv"][1]) for s in stats))})
    json.dump(me, open(os.path.join(tdir, "t1_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("таблицы записаны:", tdir, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", default="~/xdl9")
    r.add_argument("--out", default="~/xdl9/census/out")
    r.add_argument("--tables", default="~/xdl9/census/out/tables")
    r.add_argument("--threads", type=int, default=3)
    r.add_argument("--limit", type=int, default=0, help="равномерная подвыборка пар (на каждый универсум) - для пробы")
    r.add_argument("--univ", default="c1d2,ext")
    sub.add_parser("test")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if a.threads > 3:
            print("потоков не более 3 (ограничение задачи)")
            return 2
        return run(a)
    import test_census_tails
    return test_census_tails.main()


if __name__ == "__main__":
    sys.exit(main())
