#!/usr/bin/env python3
"""TK-493, П-39, перепись T2 «события фандинга» (07.11.2026). ОПИСАТЕЛЬНАЯ: статусов нет, торговли нет, правил входа/выхода нет.

Считает на Steam Deck (numpy из ~/xdl9/venv, один поток, nice 10). Не читает и не пишет ~/xdl9/c2x, c2/window, c2/out*, c2g/out*; conv_window/conv_guard не импортирует.

Вход:  ~/xdl9/funding/<биржа>/<SYM>.csv.gz (time_ms,rate,interval_h; rate - доля номинала за выплату, G7: 0,0001 = 1 bps),
       ~/xdl9/asset_pairs.csv, ~/xdl9/asset_map.csv (вселенная C2 из конвейера conv), ~/xdl9/asset_pairs_gate.csv + ~/xdl/out/gate/funding (Gate, описательно),
       свечи ~/xdl9/src/<биржа>/<SYM>.csv.gz и ~/xdl/out/gate/candles (оборот и цена в часы событий).
Определения (все числа - из задачи T2 или уже назначенные ранее; нового ничего не изобретено):
  r_h = rate*1e4/interval_h  (bps в час; interval_h берётся из файла; расхождения со строгим разрывом между выплатами печатаются).
  Часовая сетка UTC: 840 часов с 2026-10-01 00:00 (сутки 2465..2499): формирование = часы 0..335 (2465..2478), окно = 336..839 (2479..2499).
  ANTE (основное, «по последним известным ставкам»): ставка часа j = r_h последней выплаты с time <= начало_часа + 60 с (допуск 60 с - как в G7),
        действует до t_последней + interval_последней (дальше контракт не листингован -> NaN); до первой выплаты NaN.
  POST (чувствительность, «начислено за час»): ставка часа j = r_h первой выплаты с time > начало_часа + 60 с, если она покрывает час
        (t_выплаты - interval*3600с <= начало_часа + 60 с), иначе NaN. Сумма по часам = точное начисление выплат на удержании через час.
  Delta_h(пара, час) = |r_A - r_B|; серия = максимальный участок подряд идущих часов с Delta_h >= tau и одним знаком (r_A - r_B);
  сумма серии S = sum Delta_h (bps номинала одной ноги, при равных долларовых ногах); круг = 12,6 bps (4 x 3,15).
  Пары: вселенная C2 (asset_pairs.csv: между биржами, c1d2 = 1, класс крипта) = 5 830 пар, 550 активов. Серии режутся границей формирование/окно.
"""
import argparse
import bisect
import csv
import datetime
import gzip
import hashlib
import json
import os
import random
import sys
import time

import numpy as np

T0_MS = 1790812800000          # 2026-10-01 00:00 UTC
NH = 840                       # часов 01.10..04.11 (35 суток)
NHF = 336                      # формирование: часы 0..335 (сутки 2465..2478)
ND = NH // 24                  # 35
NDF = NHF // 24                # 14
DAY0 = 2465
TOL_MS = 60_000                # допуск 60 с (G7)
THR = (10, 25, 50, 100, 200)   # bps/ч (задача T2)
FEE_RT = 12.6                  # круг, bps (4 x 3,15)
GATE_MIN_UNITS = 30            # условие запуска семейства I (П-39 аудит T2)
EXS = ("binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter")
HALVES = (("form", 0, NHF), ("win", NHF, NH), ("all", 0, NH))
BANNER = "ПЕРЕПИСЬ T2 (фандинг) - ОПИСАТЕЛЬНАЯ, не торговля и не подтверждение: сутки 2465..2499 (01.10-04.11.2026), статусов нет; формирование 2465..2478 и окно 2479..2499 раздельно"


def day_str(d):
    return (datetime.date(2020, 1, 1) + datetime.timedelta(days=int(d))).strftime("%d.%m")


def hour_str(j):
    return (datetime.datetime(2026, 10, 1) + datetime.timedelta(hours=int(j))).strftime("%d.%m %H:00")


# ---------------------------------------------------------------- загрузка фандинга
def read_funding(path):
    """-> (t_ms int64[], rate float[], ih float[] (nan, если пусто)). Порядок по времени (устойчиво)."""
    t, r, ih = [], [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        next(fh, None)
        for line in fh:
            a = line.rstrip("\r\n").split(",")
            if len(a) < 2 or a[0] == "":
                continue
            t.append(int(a[0]))
            r.append(float(a[1]))
            try:
                ih.append(float(a[2]))
            except (IndexError, ValueError):
                ih.append(float("nan"))
    t = np.asarray(t, np.int64)
    r = np.asarray(r, np.float64)
    ih = np.asarray(ih, np.float64)
    o = np.argsort(t, kind="stable")
    return t[o], r[o], ih[o]


def grid_rates(t, r, ih):
    """-> (ante[NH], post[NH]) в bps/ч, nan где неизвестно. Платежи с нефинитным/нулевым interval_h исключаются."""
    ok = np.isfinite(ih) & (ih > 0) & np.isfinite(r)
    t, r, ih = t[ok], r[ok], ih[ok]
    ante = np.full(NH, np.nan)
    post = np.full(NH, np.nan)
    if len(t) == 0:
        return ante, post
    rh = r * 1e4 / ih
    tg = T0_MS + np.arange(NH, dtype=np.int64) * 3_600_000
    k = np.searchsorted(t, tg + TOL_MS, side="right") - 1
    kc = np.clip(k, 0, None)
    valid = (k >= 0)
    last_end = t[-1] + np.int64(round(ih[-1] * 3_600_000))
    # действует до t_последней + interval_последней (только для хвоста файла; внутри файла - до следующей выплаты)
    tail = (k == len(t) - 1) & (tg + TOL_MS >= last_end)
    ante = np.where(valid & ~tail, rh[kc], np.nan)
    m = np.searchsorted(t, tg + TOL_MS, side="right")      # первая выплата с time > tg + TOL
    mc = np.clip(m, 0, len(t) - 1)
    cov = (m < len(t)) & (t[mc] - np.round(ih[mc] * 3_600_000).astype(np.int64) <= tg + TOL_MS)
    post = np.where(cov, rh[mc], np.nan)
    return ante, post


# ---------------------------------------------------------------- серии (векторно)
def runs_matrix(delta, sgn, thr):
    """delta, sgn: P x H (sgn int8 +-1). -> (pair[], h0[], length[], S[]) серий подряд идущих часов с delta >= thr и одним знаком."""
    P, H = delta.shape
    with np.errstate(invalid="ignore"):
        hit = np.isfinite(delta) & (delta >= thr)
    code = np.where(hit, sgn, 0).astype(np.int8)
    pad = np.zeros((P, 1), np.int8)
    cp = np.concatenate([pad, code, pad], axis=1).ravel()
    val = np.concatenate([np.zeros((P, 1)), np.where(hit, delta, 0.0), np.zeros((P, 1))], axis=1).ravel()
    cs = np.concatenate([[0.0], np.cumsum(val)])
    b = np.flatnonzero(cp[1:] != cp[:-1]) + 1
    if len(b) < 2:
        z = np.zeros(0, np.int64)
        return z, z, z, np.zeros(0)
    st, en = b[:-1], b[1:]
    m = cp[st] != 0
    st, en = st[m], en[m]
    w = H + 2
    return st // w, st % w - 1, en - st, cs[en] - cs[st]


def quant(a, q):
    return float(np.quantile(a, q)) if len(a) else float("nan")


# ---------------------------------------------------------------- вселенные
def read_pairs(path):
    return list(csv.DictReader(open(path, encoding="utf-8", newline="")))


def pick(rows, which):
    out = []
    for r in rows:
        if r["scope"] != "между биржами" or r["big1"] != r["big2"]:
            continue
        if which == "U1":                       # вселенная C2: крипта, c1d2 = 1 (5 830 пар)
            ok = r["c1d2"] == "1" and r["big1"] == "крипта"
        elif which == "U2":                     # шире: крипта, вердикт не «разное», обе ноги основные (включая «нет данных», «пограничное»)
            ok = r["big1"] == "крипта" and not r["verdict"].startswith("разное") and r["role1"] == "main" and r["role2"] == "main"
        elif which == "U3":                     # все классы c1d2 = 1
            ok = r["c1d2"] == "1"
        else:
            raise ValueError(which)
        if ok:
            out.append(r)
    out.sort(key=lambda r: (r["key"], r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
    return out


class Store:
    """Контракты (биржа, символ) -> строки сеток ante/post; ленивая загрузка."""

    def __init__(self, funding_dirs):
        self.dirs = funding_dirs
        self.idx = {}
        self.ante = []
        self.post = []
        self.raw = {}
        self.missing = set()
        self.diag = {}

    def get(self, ex, sym):
        k = (ex, sym)
        if k in self.idx:
            return self.idx[k]
        if k in self.missing:
            return None
        p = os.path.join(self.dirs[ex], sym + ".csv.gz")
        if not os.path.exists(p):
            self.missing.add(k)
            return None
        t, r, ih = read_funding(p)
        self.raw[k] = (t, r, ih)
        a, b = grid_rates(t, r, ih)
        self.idx[k] = len(self.ante)
        self.ante.append(a)
        self.post.append(b)
        return self.idx[k]

    def matrices(self):
        return np.vstack(self.ante), np.vstack(self.post)


def build_universe(pairs, store):
    """-> dict(P, iA[], iB[], meta[]): пары, у которых есть фандинг обеих ног."""
    iA, iB, meta, nomiss = [], [], [], 0
    for r in pairs:
        a = store.get(r["ex1"], r["sym1"])
        b = store.get(r["ex2"], r["sym2"])
        if a is None or b is None:
            nomiss += 1
            continue
        iA.append(a)
        iB.append(b)
        meta.append(r)
    return dict(iA=np.asarray(iA), iB=np.asarray(iB), meta=meta, no_funding=nomiss)


# ---------------------------------------------------------------- счёт одной вселенной
def census(U, GA, label, thr=THR):
    """GA - матрица ставок (контракты x NH). -> dict таблиц."""
    meta = U["meta"]
    A = GA[U["iA"]]
    B = GA[U["iB"]]
    D = A - B
    delta = np.abs(D)
    sgn = np.where(D >= 0, 1, -1).astype(np.int8)
    P = len(meta)
    keys = np.array([m["key"] for m in meta])
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]]) if P else np.zeros(0, int)
    n_assets_all = len(starts)
    dz = np.nan_to_num(delta, nan=-1.0)
    amax = np.maximum.reduceat(dz, starts, axis=0) if P else np.zeros((0, NH))      # актив x час: максимум Delta по парам актива
    rows = []
    runs_keep = {}
    for hn, h0, h1 in HALVES:
        valid = np.isfinite(delta[:, h0:h1])
        nvalid = int(valid.sum())
        for tau in thr:
            if hn == "all":
                continue
            pr, hh, ln, S = runs_matrix(delta[:, h0:h1], sgn[:, h0:h1], tau)
            hh = hh + h0
            runs_keep[(hn, tau)] = (pr, hh, ln, S)
            with np.errstate(invalid="ignore"):
                hit = np.isfinite(delta[:, h0:h1]) & (delta[:, h0:h1] >= tau)
            nd = (h1 - h0) // 24
            pday = hit.reshape(P, nd, 24).any(axis=2)
            aday = np.logical_or.reduceat(pday, starts, axis=0) if P else np.zeros((0, nd), bool)
            ahit = amax[:, h0:h1] >= tau
            ar, ah, al, aS = runs_matrix(np.where(ahit, 1.0, np.nan), np.ones_like(ahit, dtype=np.int8), 0.5)
            rows.append(dict(universe=label, half=hn, tau=tau, pairs=P, assets_total=n_assets_all, hours=h1 - h0, pair_hours_valid=nvalid,
                             pair_hours_hit=int(hit.sum()), share_hit=(hit.sum() / nvalid if nvalid else float("nan")),
                             pairs_with_hit=int(hit.any(axis=1).sum()), runs=len(S), runs_ge2h=int((ln >= 2).sum()),
                             run_len_med=quant(ln, 0.5), run_len_q90=quant(ln, 0.9), run_len_max=int(ln.max()) if len(ln) else 0,
                             S_med=quant(S, 0.5), S_q90=quant(S, 0.9), S_max=float(S.max()) if len(S) else float("nan"),
                             runs_S_gt_fee=int((S > FEE_RT).sum()), sum_excess_over_fee=float(np.clip(S - FEE_RT, 0, None).sum()),
                             pair_day_events=int(pday.sum()), asset_day_events=int(aday.sum()), assets_with_event=int(aday.any(axis=1).sum()),
                             asset_episodes=len(al), pair_days_total=int(P * nd)))
    # итого за 35 суток: пара x сутки / актив x сутки объединением (без двойного счёта)
    for tau in thr:
        with np.errstate(invalid="ignore"):
            hit = np.isfinite(delta) & (delta >= tau)
        pday = hit.reshape(P, ND, 24).any(axis=2)
        aday = np.logical_or.reduceat(pday, starts, axis=0) if P else np.zeros((0, ND), bool)
        ahit = amax >= tau
        ar, ah, al, aS = runs_matrix(np.where(ahit, 1.0, np.nan), np.ones_like(ahit, dtype=np.int8), 0.5)
        f = [r for r in rows if r["half"] == "form" and r["tau"] == tau][0]
        w = [r for r in rows if r["half"] == "win" and r["tau"] == tau][0]
        nvalid = f["pair_hours_valid"] + w["pair_hours_valid"]
        rows.append(dict(universe=label, half="all", tau=tau, pairs=P, assets_total=n_assets_all, hours=NH, pair_hours_valid=nvalid,
                         pair_hours_hit=f["pair_hours_hit"] + w["pair_hours_hit"], share_hit=((f["pair_hours_hit"] + w["pair_hours_hit"]) / nvalid if nvalid else float("nan")),
                         pairs_with_hit=int(hit.any(axis=1).sum()), runs=f["runs"] + w["runs"], runs_ge2h=f["runs_ge2h"] + w["runs_ge2h"],
                         run_len_med=float("nan"), run_len_q90=float("nan"), run_len_max=max(f["run_len_max"], w["run_len_max"]),
                         S_med=float("nan"), S_q90=float("nan"), S_max=np.nanmax([f["S_max"], w["S_max"]]) if (f["runs"] + w["runs"]) else float("nan"),
                         runs_S_gt_fee=f["runs_S_gt_fee"] + w["runs_S_gt_fee"], sum_excess_over_fee=f["sum_excess_over_fee"] + w["sum_excess_over_fee"],
                         pair_day_events=int(pday.sum()), asset_day_events=int(aday.sum()), assets_with_event=int(aday.any(axis=1).sum()),
                         asset_episodes=len(al), pair_days_total=int(P * ND)))
    return dict(rows=rows, runs=runs_keep, A=A, B=B, delta=delta, sgn=sgn, starts=starts, keys=keys, amax=amax)


def lead_exchange(C, U, tau):
    """Для часов актива с max Delta >= tau: ведущая нога = нога с большей |r_h| в паре с максимальным Delta. -> {(half, exchange): (asset_hours, assets)}."""
    meta, delta, A, B, starts = U["meta"], C["delta"], C["A"], C["B"], C["starts"]
    P = len(meta)
    dz = np.nan_to_num(delta, nan=-1.0)
    ends = np.r_[starts[1:], P]
    cnt = {}
    ast = {}
    ar = np.arange(NH)
    for s, e in zip(starts, ends):
        sub = dz[s:e]
        am = sub.argmax(axis=0)
        mx = sub[am, ar]
        sel = np.flatnonzero(mx >= tau)
        if len(sel) == 0:
            continue
        pi = s + am[sel]
        aa = np.abs(np.nan_to_num(A[pi, sel]))
        bb = np.abs(np.nan_to_num(B[pi, sel]))
        for j, p, x, y in zip(sel, pi, aa, bb):
            m = meta[p]
            ex = m["ex1"] if x >= y else m["ex2"]
            hn = "form" if j < NHF else "win"
            cnt[(hn, ex)] = cnt.get((hn, ex), 0) + 1
            ast.setdefault((hn, ex), set()).add(m["key"])
    return {k: (v, len(ast[k])) for k, v in cnt.items()}


# ---------------------------------------------------------------- свечи (оборот и цена)
class Candles:
    def __init__(self, dirs):
        self.dirs = dirs
        self.cache = {}

    def get(self, ex, sym):
        k = (ex, sym)
        if k in self.cache:
            return self.cache[k]
        p = os.path.join(self.dirs[ex], sym + ".csv.gz")
        out = None
        if os.path.exists(p):
            tt, cc, qq, vv = [], [], [], []
            qrows = 0
            with gzip.open(p, "rt", encoding="utf-8", newline="") as fh:
                next(fh, None)
                for line in fh:
                    a = line.rstrip("\r\n").split(",")
                    if len(a) < 6 or a[0] == "":
                        continue
                    tt.append(int(a[0]))
                    cc.append(float(a[4]))
                    vv.append(float(a[5]))
                    q = a[6] if len(a) > 6 else ""
                    if q != "":
                        qrows += 1
                        qq.append(float(q))
                    else:
                        qq.append(0.0)
            tt = np.asarray(tt, np.int64)
            cc = np.asarray(cc)
            vv = np.asarray(vv)
            qq = np.asarray(qq)
            turn = qq if qrows > 0 else cc * vv                      # как asset_extract: quote_v, если у файла он есть, иначе c*v
            nm = NH * 60
            mi = (tt - T0_MS) // 60_000
            ok = (mi >= 0) & (mi < nm)
            price = np.full(nm, np.nan)
            tmin = np.zeros(nm)
            good = ok & (vv > 0) & (cc > 0)
            price[mi[good]] = cc[good]
            tmin[mi[ok & (vv > 0)]] = turn[ok & (vv > 0)]
            out = (tmin.reshape(NH, 60).sum(axis=1), price, "quote_v" if qrows > 0 else "c*v")
        self.cache[k] = out
        return out


def capacity(run, C_, cd):
    """run: dict(exA,symA,exB,symB,h0,len). Оборот ног по часам серии (USD), цена спреда: s = 1e4*ln(cA/cB) на общих минутах (обе v>0)."""
    a = cd.get(run["exA"], run["symA"])
    b = cd.get(run["exB"], run["symB"])
    if a is None or b is None:
        return dict(turn_A_usd=float("nan"), turn_B_usd=float("nan"), turn_min_hours_usd=float("nan"), turn_min_hour_min_usd=float("nan"),
                    common_min=0, s_start=float("nan"), s_end=float("nan"), ds_max=float("nan"), turn_src="нет свечей")
    h0, L = run["h0"], run["len"]
    ta, tb = a[0][h0:h0 + L], b[0][h0:h0 + L]
    tm = np.minimum(ta, tb)
    pa = a[1][h0 * 60:(h0 + L) * 60]
    pb = b[1][h0 * 60:(h0 + L) * 60]
    okm = np.isfinite(pa) & np.isfinite(pb)
    if okm.any():
        s = 1e4 * np.log(pa[okm] / pb[okm])
        s0, s1 = float(s[0]), float(s[-1])
        dsm = float(np.abs(s - s0).max())
    else:
        s0 = s1 = dsm = float("nan")
    return dict(turn_A_usd=float(ta.sum()), turn_B_usd=float(tb.sum()), turn_min_hours_usd=float(tm.sum()), turn_min_hour_min_usd=float(tm.min()),
                common_min=int(okm.sum()), s_start=s0, s_end=s1, ds_max=dsm, turn_src="%s/%s" % (a[2], b[2]))


def run_rows(C, U, tau, hn_list=("form", "win"), cd=None, rank_by="S", limit=None):
    """Серии (пара-уровень) -> список dict; при cd - добавляется ёмкость."""
    meta = U["meta"]
    out = []
    for hn in hn_list:
        pr, hh, ln, S = C["runs"][(hn, tau)]
        for p, h0, L, s in zip(pr, hh, ln, S):
            m = meta[p]
            dl = C["delta"][p, h0:h0 + L]
            ra, rb = C["A"][p, h0], C["B"][p, h0]
            high = m["ex1"] if ra >= rb else m["ex2"]            # у кого ставка выше: там шорт получает (положительная ставка: лонги платят шортам)
            lead = m["ex1"] if abs(ra) >= abs(rb) else m["ex2"]
            out.append(dict(key=m["key"], exA=m["ex1"], symA=m["sym1"], exB=m["ex2"], symB=m["sym2"], half=hn, h0=int(h0), start=hour_str(h0), len=int(L),
                            delta_max=float(dl.max()), delta_mean=float(dl.mean()), S=float(s), rA_start=float(ra), rB_start=float(rb), short_leg=high, lead_leg=lead))
    out.sort(key=lambda r: -r["S"])
    if limit:
        out = out[:limit]
    if cd is not None:
        for r in out:
            r.update(capacity(r, C, cd))
    return out


# ---------------------------------------------------------------- вывод
def write_csv(path, rows, cols, banner=BANNER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# " + banner + "\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c, "")) for c in cols])


def fmt(v):
    if isinstance(v, float):
        if v != v:
            return ""
        return "%.6g" % v
    return v


def sha256f(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for ch in iter(lambda: fh.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


# ---------------------------------------------------------------- самопроверка: наивный пересчёт формулы (bisect, циклы)
def naive_ante_post(t, r, ih):
    ante = [float("nan")] * NH
    post = [float("nan")] * NH
    pts = [(int(a), float(b) * 1e4 / float(c), float(c)) for a, b, c in zip(t, r, ih) if c == c and c > 0]
    pts.sort()
    ts = [p[0] for p in pts]
    for j in range(NH):
        tg = T0_MS + j * 3_600_000
        k = bisect.bisect_right(ts, tg + TOL_MS) - 1
        if k >= 0:
            lastend = ts[-1] + int(round(pts[-1][2] * 3_600_000))
            if not (k == len(ts) - 1 and tg + TOL_MS >= lastend):
                ante[j] = pts[k][1]
        m = bisect.bisect_right(ts, tg + TOL_MS)
        if m < len(ts):
            if ts[m] - int(round(pts[m][2] * 3_600_000)) <= tg + TOL_MS:
                post[j] = pts[m][1]
    return ante, post


def naive_runs(da, db, thr):
    """da, db - списки ставок A, B по часам [h0,h1). -> список (h0, len, S) серий одного знака."""
    out = []
    j = 0
    n = len(da)
    while j < n:
        d = da[j] - db[j]
        if d == d and abs(d) >= thr:
            sg = d > 0
            k = j
            s = 0.0
            while k < n:
                dd = da[k] - db[k]
                if not (dd == dd and abs(dd) >= thr and (dd > 0) == sg):
                    break
                s += abs(dd)
                k += 1
            out.append((j, k - j, s))
            j = k
        else:
            j += 1
    return out


def selfcheck(store, U, C, which, seed=37, n=300):
    """Сверка векторной сетки и серий с наивным пересчётом на n случайных парах (+ все пары BIRB)."""
    rnd = random.Random(seed)
    meta = U["meta"]
    pool = list(range(len(meta)))
    pick_ = set(rnd.sample(pool, min(n, len(pool))))
    pick_ |= {i for i, m in enumerate(meta) if m["key"] == "BIRB"}
    bad_grid = 0
    bad_runs = 0
    checked = 0
    GA, GP = store.matrices()
    GM = GA if which == "ante" else GP
    for i in sorted(pick_):
        m = meta[i]
        ra, rb = [], []
        for ex, sym in ((m["ex1"], m["sym1"]), (m["ex2"], m["sym2"])):
            t, r, ih = store.raw[(ex, sym)]
            a, b = naive_ante_post(t, r, ih)
            ra.append(a if which == "ante" else b)
        ia, ib = U["iA"][i], U["iB"][i]
        for gi, nv in ((ia, ra[0]), (ib, ra[1])):
            v = GM[gi]
            nvv = np.asarray(nv)
            if not np.allclose(np.nan_to_num(v, nan=-9e9), np.nan_to_num(nvv, nan=-9e9), rtol=0, atol=1e-9):
                bad_grid += 1
        for hn, h0, h1 in (("form", 0, NHF), ("win", NHF, NH)):
            for tau in THR:
                nr = naive_runs(ra[0][h0:h1], ra[1][h0:h1], tau)
                pr, hh, ln, S = C["runs"][(hn, tau)]
                sel = pr == i
                vr = sorted((int(a) - h0, int(b), float(c)) for a, b, c in zip(hh[sel], ln[sel], S[sel]))
                nr = sorted(nr)
                same = len(vr) == len(nr) and all(x[0] == y[0] and x[1] == y[1] and abs(x[2] - y[2]) <= 1e-6 for x, y in zip(vr, nr))
                if not same:
                    bad_runs += 1
        checked += 1
    return dict(which=which, pairs_checked=checked, grid_mismatch=bad_grid, runs_mismatch=bad_runs)


# ---------------------------------------------------------------- устойчивость и срезы (ante)
def leave_one_out(C, U, taus):
    """События при исключении всех пар с участием одной биржи. -> строки (excluded '-' = ничего не исключено)."""
    meta = U["meta"]
    exA = np.array([m["ex1"] for m in meta])
    exB = np.array([m["ex2"] for m in meta])
    keys_all = np.array([m["key"] for m in meta])
    rows = []
    for X in (None,) + EXS:
        keep = np.ones(len(meta), bool) if X is None else ((exA != X) & (exB != X))
        d = C["delta"][keep]
        keys = keys_all[keep]
        starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        for tau in taus:
            with np.errstate(invalid="ignore"):
                hit = np.isfinite(d) & (d >= tau)
            pday = hit.reshape(len(d), ND, 24).any(axis=2)
            aday = np.logical_or.reduceat(pday, starts, axis=0)
            for hn, d0, d1 in (("form", 0, NDF), ("win", NDF, ND), ("all", 0, ND)):
                rows.append(dict(excluded=X or "-", tau=tau, half=hn, pairs=int(keep.sum()), pair_day_events=int(pday[:, d0:d1].sum()),
                                 asset_day_events=int(aday[:, d0:d1].sum()), assets=int(aday[:, d0:d1].any(axis=1).sum())))
    return rows


def daily_events(C, U, taus):
    """Суточный ряд: события пара x сутки, актив x сутки, максимум Delta_h за сутки."""
    P = len(U["meta"])
    starts = C["starts"]
    dm = np.nanmax(np.where(np.isfinite(C["delta"]), C["delta"], np.nan).reshape(P, ND, 24), axis=(0, 2))
    rows = []
    for tau in taus:
        with np.errstate(invalid="ignore"):
            hit = np.isfinite(C["delta"]) & (C["delta"] >= tau)
        pday = hit.reshape(P, ND, 24).any(axis=2)
        aday = np.logical_or.reduceat(pday, starts, axis=0)
        for d in range(ND):
            rows.append(dict(tau=tau, day=DAY0 + d, date=day_str(DAY0 + d), half="form" if d < NDF else "win", pair_day_events=int(pday[:, d].sum()),
                             asset_day_events=int(aday[:, d].sum()), delta_max=float(dm[d])))
    return rows


def asset_events(rr):
    """Серии (пара-уровень) -> по активу: число серий, часы, сумма S, максимум Delta, суток, ведущая нога (мода)."""
    g = {}
    for r in rr:
        g.setdefault(r["key"], []).append(r)
    rows = []
    for k, lst in g.items():
        leads = {}
        for r in lst:
            leads[r["lead_leg"]] = leads.get(r["lead_leg"], 0) + 1
        lm = max(leads.items(), key=lambda x: (x[1], x[0]))
        days = sorted({(r["h0"] + j) // 24 for r in lst for j in range(r["len"])})
        exs = sorted({r["exA"] for r in lst} | {r["exB"] for r in lst})
        rows.append(dict(key=k, runs=len(lst), pair_hours_runs=sum(r["len"] for r in lst), S_sum=sum(r["S"] for r in lst), delta_max=max(r["delta_max"] for r in lst),
                         days=len(days), first=day_str(DAY0 + days[0]), last=day_str(DAY0 + days[-1]), lead_mode=lm[0], lead_mode_share=lm[1] / len(lst), exchanges=" ".join(exs)))
    rows.sort(key=lambda r: -r["S_sum"])
    return rows


SIZE_LO, SIZE_HI = 3000.0, 80000.0      # позиции продавца 3 тыс.-80 тыс. $ (видео; аудит xdl9-video-audit-2026-11-07 §2 строка 7)


def assetday_best(rr):
    """Серии с ёмкостью -> по (актив, сутки начала): лучшая по S, число пар, часы объединением, доли размеров продавца от оборота тонкой ноги в самый тихий час."""
    g = {}
    for r in rr:
        g.setdefault((r["key"], r["h0"] // 24), []).append(r)
    rows = []
    for (k, d), lst in sorted(g.items()):
        b = max(lst, key=lambda r: r["S"])
        hrs = set()
        for r in lst:
            hrs.update(range(r["h0"], r["h0"] + r["len"]))
        tm = b["turn_min_hour_min_usd"]
        rows.append(dict(key=k, date=b["start"], pairs_in_event=len(lst), hours=len(hrs), delta_max=max(r["delta_max"] for r in lst), exA=b["exA"], exB=b["exB"],
                         short_leg=b["short_leg"], lead_leg=b["lead_leg"], S=b["S"], turn_min_hour_min_usd=tm, turn_min_hours_usd=b["turn_min_hours_usd"],
                         share3k=(SIZE_LO / tm if tm == tm and tm > 0 else float("nan")), share80k=(SIZE_HI / tm if tm == tm and tm > 0 else float("nan")),
                         ds_max=b["ds_max"], s_start=b["s_start"], rA_start=b["rA_start"], rB_start=b["rB_start"]))
    return rows


SELLER = ("WCT", "H", "LAB", "BIRB")


def seller_assets(store, amap, U2, GA):
    """Активы из видео продавца: ставки ног (r_h, bps/ч) и Delta_h пар вселенной U2 (ante)."""
    legs, prs = [], []
    for K in SELLER:
        for (ex, sym), key in sorted(amap.items()):
            if key != K or ex not in EXS:
                continue
            if (ex, sym) not in store.raw:
                p = os.path.join(store.dirs[ex], sym + ".csv.gz")
                if not os.path.exists(p):
                    continue
                store.raw[(ex, sym)] = read_funding(p)
            t, r, ih = store.raw[(ex, sym)]
            ok = np.isfinite(ih) & (ih > 0)
            if not ok.any():
                continue
            rh = r[ok] * 1e4 / ih[ok]
            tt = t[ok]
            ihs = ih[ok]
            fm = tt < T0_MS + NHF * 3_600_000
            for hn, m in (("form", fm), ("win", ~fm)):
                if not m.any():
                    continue
                j = int(np.argmax(np.abs(rh[m])))
                legs.append(dict(key=K, exchange=ex, symbol=sym, half=hn, payments=int(m.sum()), interval_h=" ".join("%g" % v for v in sorted(set(ihs[m].tolist()))),
                                 r_h_mean=float(rh[m].mean()), r_h_min=float(rh[m].min()), r_h_max=float(rh[m].max()), abs_max=float(np.abs(rh[m]).max()),
                                 abs_max_at=datetime.datetime.fromtimestamp(tt[m][j] / 1000, datetime.timezone.utc).strftime("%d.%m %H:%M")))
    for i, m in enumerate(U2["meta"]):
        if m["key"] not in SELLER:
            continue
        a_, b_ = GA[U2["iA"][i]], GA[U2["iB"][i]]
        d = np.abs(a_ - b_)
        for hn, h0, h1 in (("form", 0, NHF), ("win", NHF, NH)):
            x = d[h0:h1]
            fin = np.isfinite(x)
            if not fin.any():
                continue
            j = int(np.nanargmax(np.where(fin, x, -1)))
            prs.append(dict(key=m["key"], pair="%s/%s" % (m["ex1"], m["ex2"]), verdict=m["verdict"][:14], in_U1=int(m["c1d2"] == "1"), half=hn, hours_valid=int(fin.sum()),
                            delta_max=float(x[j]), at=hour_str(h0 + j), rA_at=float(a_[h0 + j]), rB_at=float(b_[h0 + j]), h_ge10=int((x[fin] >= 10).sum()),
                            h_ge25=int((x[fin] >= 25).sum()), h_ge50=int((x[fin] >= 50).sum()), h_ge100=int((x[fin] >= 100).sum())))
    return legs, prs


# ---------------------------------------------------------------- устойчивость сигнала: известная ставка (ante) против начисленной (post)
def persistence(Ca, Cp, taus):
    """Для серий ante (U1): сумма начисленной Delta в НАПРАВЛЕНИИ ante по тем же часам. real_j = sign_ante_j * (post_A - post_B)_j (nan, если начисление за час неизвестно).
    Печатает на уровне часов и серий. Критерий сравнения серии - круг 12,6 bps."""
    PA, PB = Cp["A"], Cp["B"]
    real = Ca["sgn"].astype(np.float64) * (PA - PB)
    rows = []
    for tau in taus:
        for hn, h0, h1 in (("form", 0, NHF), ("win", NHF, NH), ("all", 0, NH)):
            hours = []
            runs = []
            for hh_ in (("form", "win") if hn == "all" else (hn,)):
                pr, hh, ln, S = Ca["runs"][(hh_, tau)]
                for p, a, L, s in zip(pr, hh, ln, S):
                    x = real[p, a:a + L]
                    hours.extend(x.tolist())
                    ok = np.isfinite(x)
                    runs.append((float(s), float(x[ok].sum()) if ok.all() else float("nan"), int(ok.sum()), int(L)))
            hv = np.array([v for v in hours if v == v])
            full = [(s, r) for s, r, k, L in runs if k == L]
            sa = np.array([s for s, r in full])
            ra = np.array([r for s, r in full])
            rows.append(dict(tau=tau, half=hn, hit_hours=len(hours), hit_hours_with_accrual=int(len(hv)), real_mean_per_hour=float(hv.mean()) if len(hv) else float("nan"),
                             real_median_per_hour=float(np.median(hv)) if len(hv) else float("nan"), share_hours_real_gt0=float((hv > 0).mean()) if len(hv) else float("nan"),
                             share_hours_real_ge_tau=float((hv >= tau).mean()) if len(hv) else float("nan"),
                             runs=len(runs), runs_full_accrual=len(full), S_ante_sum=float(sa.sum()) if len(sa) else float("nan"), R_post_sum=float(ra.sum()) if len(ra) else float("nan"),
                             R_median=float(np.median(ra)) if len(ra) else float("nan"), share_runs_R_gt0=float((ra > 0).mean()) if len(ra) else float("nan"),
                             share_runs_R_gt_fee=float((ra > FEE_RT).mean()) if len(ra) else float("nan"), R_gt_fee_runs=int((ra > FEE_RT).sum()) if len(ra) else 0))
    return rows


# ---------------------------------------------------------------- главная
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/xdl9"))
    ap.add_argument("--raw", default=os.path.expanduser("~/xdl/out"))
    ap.add_argument("--out", default=os.path.expanduser("~/xdl9/census/t2"))
    ap.add_argument("--sample", type=int, default=300)
    a = ap.parse_args()
    t_start = time.time()
    os.makedirs(a.out, exist_ok=True)
    fdirs = {ex: os.path.join(a.root, "funding", ex) for ex in EXS}
    fdirs["gate"] = os.path.join(a.raw, "gate", "funding")
    cdirs = {ex: os.path.join(a.root, "src", ex) for ex in EXS}
    cdirs["gate"] = os.path.join(a.raw, "gate", "candles")
    log = open(os.path.join(a.out, "run.log"), "w", encoding="utf-8")

    def say(*x):
        s = " ".join(str(i) for i in x)
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()

    pairs_path = os.path.join(a.root, "asset_pairs.csv")
    allrows = read_pairs(pairs_path)
    say("asset_pairs.csv sha256", sha256f(pairs_path)[:16], "строк", len(allrows))
    store = Store(fdirs)
    univ = {}
    for name in ("U1", "U2", "U3"):
        univ[name] = build_universe(pick(allrows, name), store)
        say(name, "пар с фандингом обеих ног", len(univ[name]["meta"]), "без фандинга", univ[name]["no_funding"], "активов", len({m["key"] for m in univ[name]["meta"]}))
    gpath = os.path.join(a.root, "asset_pairs_gate.csv")
    grows = [r for r in read_pairs(gpath) if r["scope"] == "между биржами" and r["big1"] == r["big2"] == "крипта" and r["c1d2"] == "1" and "gate" in (r["ex1"], r["ex2"])]
    grows.sort(key=lambda r: (r["key"], r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
    univ["G"] = build_universe(grows, store)
    say("G (Gate, крипта, c1d2=1) пар с фандингом", len(univ["G"]["meta"]), "без", univ["G"]["no_funding"])

    # ---- диагностика ног: интервалы, расхождение с разрывом, распределение ставок (все файлы 8 бирж + Gate)
    leg_rows, pay_rows = [], []
    amap = {}
    for r in csv.DictReader(open(os.path.join(a.root, "asset_map.csv"), encoding="utf-8", newline="")):
        amap[(r["exchange"], r["symbol"])] = r["key"]
    for ex in EXS + ("gate",):
        files = sorted(f[:-7] for f in os.listdir(fdirs[ex]) if f.endswith(".csv.gz"))
        npay = nbad = nnan = 0
        ihc = {}
        absr = []
        contracts = 0
        thr_ch = {tau: 0 for tau in THR}
        pay_ge = {(hn, tau): [0, set(), set()] for hn in ("form", "win") for tau in THR}
        for sym in files:
            t, r, ih = read_funding(os.path.join(fdirs[ex], sym + ".csv.gz"))
            if len(t) == 0:
                continue
            contracts += 1
            npay += len(t)
            nnan += int((~np.isfinite(ih)).sum())
            gap = np.diff(t) / 3.6e6
            if len(gap):
                nbad += int((np.abs(gap - ih[1:]) > TOL_MS / 3.6e6).sum())
            for v in np.unique(ih[np.isfinite(ih)]):
                ihc[float(v)] = ihc.get(float(v), 0) + int((ih == v).sum())
            ok = np.isfinite(ih) & (ih > 0)
            rh = np.abs(r[ok]) * 1e4 / ih[ok]
            absr.append(rh)
            tm = t[ok]
            rp = np.abs(r[ok]) * 1e4                  # bps за выплату
            key = amap.get((ex, sym), "%s:%s" % (ex, sym))
            for hn, lo, hi in (("form", T0_MS, T0_MS + NHF * 3_600_000), ("win", T0_MS + NHF * 3_600_000, T0_MS + NH * 3_600_000)):
                inwin = (tm >= lo) & (tm < hi)
                for tau in THR:
                    c = int((inwin & (rp >= tau)).sum())
                    if c:
                        pay_ge[(hn, tau)][0] += c
                        pay_ge[(hn, tau)][1].add((ex, sym))
                        pay_ge[(hn, tau)][2].add(key)
        absr = np.concatenate(absr) if absr else np.zeros(0)
        tot = sum(ihc.values())
        leg_rows.append(dict(exchange=ex, contracts=contracts, payments=npay, interval_h_empty=nnan, gap_ne_interval=nbad,
                             share_int_1h=ihc.get(1.0, 0) / tot, share_int_4h=ihc.get(4.0, 0) / tot, share_int_8h=ihc.get(8.0, 0) / tot,
                             share_int_other=1 - (ihc.get(1.0, 0) + ihc.get(4.0, 0) + ihc.get(8.0, 0)) / tot,
                             absrh_p50=quant(absr, 0.5), absrh_p90=quant(absr, 0.9), absrh_p99=quant(absr, 0.99), absrh_p999=quant(absr, 0.999),
                             absrh_max=float(absr.max()) if len(absr) else float("nan"), pay_ge10_bps_per_h=int((absr >= 10).sum()), pay_ge25_bps_per_h=int((absr >= 25).sum()),
                             pay_ge50_bps_per_h=int((absr >= 50).sum()), pay_ge100_bps_per_h=int((absr >= 100).sum()), pay_ge200_bps_per_h=int((absr >= 200).sum())))
        for (hn, tau), v in pay_ge.items():
            pay_rows.append(dict(exchange=ex, half=hn, tau_bps_per_payment=tau, payments=v[0], contracts=len(v[1]), assets=len(v[2])))
        say("ноги", ex, "контрактов", contracts, "выплат", npay, "interval_h пусто", nnan, "разрыв != interval_h", nbad)
    write_csv(os.path.join(a.out, "t2_legs.csv"), leg_rows, list(leg_rows[0].keys()))
    write_csv(os.path.join(a.out, "t2_payments_ge.csv"), pay_rows, list(pay_rows[0].keys()))

    GA, GP = store.matrices()
    say("контрактов в сетке", GA.shape[0], "часов", NH)
    cd = Candles(cdirs)
    summ_rows, lead_rows, cls_rows, exp_rows = [], [], [], []
    CC = {}
    for est, G in (("ante", GA), ("post", GP)):
        for name in ("U1", "U2", "U3", "G"):
            U = univ[name]
            C = census(U, G, name + "_" + est)
            CC[(est, name)] = C
            if name == "U2":
                for kk in ("A", "B", "delta", "sgn", "amax"):
                    C[kk] = None
            for r in C["rows"]:
                r["estimator"] = est
                summ_rows.append(r)
            say("census", est, name, "пар", len(U["meta"]))
    cols = ["universe", "estimator", "half", "tau", "pairs", "assets_total", "hours", "pair_hours_valid", "pair_hours_hit", "share_hit", "pairs_with_hit", "runs", "runs_ge2h",
            "run_len_med", "run_len_q90", "run_len_max", "S_med", "S_q90", "S_max", "runs_S_gt_fee", "sum_excess_over_fee", "pair_day_events", "asset_day_events",
            "assets_with_event", "asset_episodes", "pair_days_total"]
    for r in summ_rows:
        r["universe"] = r["universe"].split("_")[0]
    write_csv(os.path.join(a.out, "t2_counts.csv"), summ_rows, cols)

    # ---- по классам и парам бирж (U3 ante: все классы; U1: пары бирж)
    for est in ("ante", "post"):
        C = CC[(est, "U3")]
        U = univ["U3"]
        cls = np.array([m["big1"] for m in U["meta"]])
        for cn in sorted(set(cls)):
            sel = np.flatnonzero(cls == cn)
            for tau in (25, 100):
                for hn, h0, h1 in (("form", 0, NHF), ("win", NHF, NH)):
                    d = C["delta"][sel][:, h0:h1]
                    with np.errstate(invalid="ignore"):
                        hit = np.isfinite(d) & (d >= tau)
                    pday = hit.reshape(len(sel), (h1 - h0) // 24, 24).any(axis=2)
                    keys = np.array([U["meta"][i]["key"] for i in sel])
                    cls_rows.append(dict(estimator=est, klass=cn, half=hn, tau=tau, pairs=len(sel), pair_hours_hit=int(hit.sum()), pair_day_events=int(pday.sum()),
                                         assets=len(set(keys[pday.any(axis=1)]))))
        C = CC[(est, "U1")]
        U = univ["U1"]
        epair = np.array([("%s~%s" % tuple(sorted((m["ex1"], m["ex2"])))) for m in U["meta"]])
        for ep in sorted(set(epair)):
            sel = np.flatnonzero(epair == ep)
            for tau in (25, 100):
                for hn, h0, h1 in (("form", 0, NHF), ("win", NHF, NH)):
                    d = C["delta"][sel][:, h0:h1]
                    with np.errstate(invalid="ignore"):
                        hit = np.isfinite(d) & (d >= tau)
                    pday = hit.reshape(len(sel), (h1 - h0) // 24, 24).any(axis=2)
                    keys = np.array([U["meta"][i]["key"] for i in sel])
                    exp_rows.append(dict(estimator=est, exchange_pair=ep, half=hn, tau=tau, pairs=len(sel), pair_hours_hit=int(hit.sum()), pair_day_events=int(pday.sum()),
                                         assets=len(set(keys[pday.any(axis=1)]))))
    write_csv(os.path.join(a.out, "t2_by_class.csv"), cls_rows, list(cls_rows[0].keys()))
    write_csv(os.path.join(a.out, "t2_by_exchange_pair.csv"), exp_rows, list(exp_rows[0].keys()))

    # ---- ведущая нога (ante, U1) на tau = 25, 50, 100, 200
    for est in ("ante", "post"):
        for tau in (25, 50, 100, 200):
            for (hn, ex), (ah, na) in sorted(lead_exchange(CC[(est, "U1")], univ["U1"], tau).items()):
                lead_rows.append(dict(estimator=est, tau=tau, half=hn, exchange=ex, asset_hours=ah, assets=na))
    write_csv(os.path.join(a.out, "t2_lead_exchange.csv"), lead_rows, ["estimator", "tau", "half", "exchange", "asset_hours", "assets"])

    # ---- серии: топ-20 по S (ante и post, U1), все серии с tau = 100 (ёмкость), 25 без ёмкости (только на Deck)
    rcols = ["rank", "key", "exA", "symA", "exB", "symB", "half", "start", "len", "delta_max", "delta_mean", "S", "rA_start", "rB_start", "short_leg", "lead_leg",
             "turn_A_usd", "turn_B_usd", "turn_min_hours_usd", "turn_min_hour_min_usd", "common_min", "s_start", "s_end", "ds_max", "turn_src"]
    for est in ("ante", "post"):
        C = CC[(est, "U1")]
        U = univ["U1"]
        for tau in (25,):
            rr = run_rows(C, U, tau, cd=None)
            top = rr[:20]
            for r in top:
                r.update(capacity(r, C, cd))
            for i, r in enumerate(top):
                r["rank"] = i + 1
            write_csv(os.path.join(a.out, "t2_top20_%s_tau%d.csv" % (est, tau)), top, rcols)
            write_csv(os.path.join(a.out, "t2_runs_tau%d_%s_all.csv" % (tau, est)), rr, [c for c in rcols if c not in ("rank",) and not c.startswith("turn") and c not in ("common_min", "s_start", "s_end", "ds_max")])
        for tau in (50, 100, 200):
            rr = run_rows(C, U, tau, cd=cd, limit=300 if tau == 50 else None)
            for i, r in enumerate(rr):
                r["rank"] = i + 1
            write_csv(os.path.join(a.out, "t2_runs_tau%d_%s.csv" % (tau, est)), rr, rcols)
            say("серии", est, "tau", tau, len(rr))
    # ---- Gate (описательно): серии tau = 25 и 100 без ёмкости
    for est in ("ante",):
        C = CC[(est, "G")]
        U = univ["G"]
        for tau in (25, 100):
            rr = run_rows(C, U, tau, cd=None)
            for i, r in enumerate(rr):
                r["rank"] = i + 1
            write_csv(os.path.join(a.out, "t2_gate_runs_tau%d.csv" % tau), rr[:200], ["rank"] + [c for c in rcols[1:16]])

    # ---- устойчивость: без одной биржи, суточный ряд, концентрация по активам, события tau = 100 по активу-суткам, активы продавца
    C1, U1_ = CC[("ante", "U1")], univ["U1"]
    for est in ("ante", "post"):
        loo = leave_one_out(CC[(est, "U1")], U1_, (25, 100))
        for r in loo:
            r["estimator"] = est
        write_csv(os.path.join(a.out, "t2_leave_one_exchange_%s.csv" % est), loo, ["estimator", "excluded", "tau", "half", "pairs", "pair_day_events", "asset_day_events", "assets"])
    write_csv(os.path.join(a.out, "t2_daily_events.csv"), daily_events(C1, U1_, (25, 50, 100)), ["tau", "day", "date", "half", "pair_day_events", "asset_day_events", "delta_max"])
    ar = run_rows(C1, U1_, 25, cd=None)
    write_csv(os.path.join(a.out, "t2_asset_events_tau25.csv"), asset_events(ar), ["key", "runs", "pair_hours_runs", "S_sum", "delta_max", "days", "first", "last", "lead_mode", "lead_mode_share", "exchanges"])
    r100 = run_rows(C1, U1_, 100, cd=cd)
    r100b = assetday_best(r100)
    write_csv(os.path.join(a.out, "t2_events_tau100_assetday.csv"), r100b, ["key", "date", "pairs_in_event", "hours", "delta_max", "exA", "exB", "short_leg", "lead_leg", "S", "turn_min_hour_min_usd", "turn_min_hours_usd",
                                                                          "share3k", "share80k", "ds_max", "s_start", "rA_start", "rB_start"])
    per = persistence(CC[("ante", "U1")], CC[("post", "U1")], (25, 50, 100))
    write_csv(os.path.join(a.out, "t2_persistence.csv"), per, list(per[0].keys()))
    sl, sp = seller_assets(store, amap, univ["U2"], GA)
    write_csv(os.path.join(a.out, "t2_seller_legs.csv"), sl, list(sl[0].keys()) if sl else ["empty"])
    write_csv(os.path.join(a.out, "t2_seller_pairs.csv"), sp, list(sp[0].keys()) if sp else ["empty"])

    # ---- BIRB 23.10: выплаты ног и почасовые Delta (U1, ante/post)
    birb = []
    for (ex, sym), (t, r, ih) in sorted(store.raw.items()):
        base = amap.get((ex, sym), "")
        if base != "BIRB" and not sym.upper().startswith("BIRB"):
            continue
        for tt, rr_, ii in zip(t, r, ih):
            if T0_MS + 21 * 86_400_000 <= tt < T0_MS + 24 * 86_400_000:      # 22.10..24.10 UTC
                birb.append(dict(_t=int(tt), exchange=ex, symbol=sym, time_utc=datetime.datetime.fromtimestamp(tt / 1000, datetime.timezone.utc).strftime("%d.%m %H:%M:%S"),
                                 rate_pct=rr_ * 100, interval_h=ii, r_h_bps=rr_ * 1e4 / ii))
    birb.sort(key=lambda x: (x["exchange"], x["_t"]))
    write_csv(os.path.join(a.out, "t2_birb_payments.csv"), birb, ["exchange", "symbol", "time_utc", "rate_pct", "interval_h", "r_h_bps"])
    brows = []
    for est in ("ante", "post"):
        C = CC[(est, "U1")]
        U = univ["U1"]
        for i, m in enumerate(U["meta"]):
            if m["key"] != "BIRB":
                continue
            d = C["delta"][i]
            j0 = 22 * 24
            seg = d[j0:j0 + 48]
            fin = np.isfinite(seg)
            if not fin.any():
                continue
            jm = int(np.nanargmax(np.where(fin, seg, -1)))
            brows.append(dict(estimator=est, pair="%s/%s" % (m["ex1"], m["ex2"]), delta_max_bps_h=float(seg[jm]), at=hour_str(j0 + jm),
                              hours_ge25=int((seg >= 25).sum()), hours_ge50=int((seg >= 50).sum()), hours_ge100=int((seg >= 100).sum()), hours_ge200=int((seg >= 200).sum()),
                              sum_delta_bps=float(np.nansum(seg)), rA_at=float(C["A"][i, j0 + jm]), rB_at=float(C["B"][i, j0 + jm])))
    write_csv(os.path.join(a.out, "t2_birb_pairs.csv"), brows, list(brows[0].keys()) if brows else ["empty"])

    # ---- условие запуска семейства I
    cond = {}
    for est in ("ante", "post"):
        for name in ("U1", "U2", "U3", "G"):
            for r in summ_rows:
                if r["universe"] == name and r["estimator"] == est and r["tau"] == 25 and r["half"] in ("form", "win", "all"):
                    cond.setdefault(est + "_" + name, {})[r["half"]] = dict(pair_day_events=r["pair_day_events"], asset_day_events=r["asset_day_events"],
                                                                            assets=r["assets_with_event"], asset_episodes=r["asset_episodes"], runs=r["runs"],
                                                                            pair_hours=r["pair_hours_hit"], pairs_with_hit=r["pairs_with_hit"])
    cond["rule"] = "семейство I запускается, если >= %d независимых событий «пара x сутки» с Delta_h >= 25 bps/ч (П-39, аудит T2 §7)" % GATE_MIN_UNITS
    cond["met_ante_U1_all_pair_day"] = bool(cond["ante_U1"]["all"]["pair_day_events"] >= GATE_MIN_UNITS)
    cond["met_ante_U1_all_asset_day"] = bool(cond["ante_U1"]["all"]["asset_day_events"] >= GATE_MIN_UNITS)
    cond["met_ante_U1_all_assets"] = bool(cond["ante_U1"]["all"]["assets"] >= GATE_MIN_UNITS)
    cond["met_post_U1_all_pair_day"] = bool(cond["post_U1"]["all"]["pair_day_events"] >= GATE_MIN_UNITS)
    json.dump(dict(_banner=BANNER, **cond), open(os.path.join(a.out, "t2_gate_condition.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- самопроверки
    sc = []
    for est in ("ante", "post"):
        s = selfcheck(store, univ["U1"], CC[(est, "U1")], est, n=a.sample)
        sc.append(s)
        say("самопроверка", s)
    # единицы: BTC годовая ставка (G7: Binance 5,45 %, Bybit 5,25 %, OKX 6,22 %, BingX 8,74 %, Bitget 6,93 %, Kraken 10,55 %, Aster 6,70 %, Lighter 8,01 %)
    btc = {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "bitget": "BTCUSDT", "kraken": "PF_XBTUSD", "aster": "BTCUSDT", "lighter": "BTC"}
    units = []
    for ex, sym in btc.items():
        k = (ex, sym)
        if k not in store.raw:
            store.get(ex, sym)
        t, r, ih = store.raw[k]
        ok = (t >= T0_MS) & (t < T0_MS + NH * 3_600_000)
        ann = float(np.mean(r[ok] / ih[ok]) * 8760 * 100)
        units.append(dict(exchange=ex, symbol=sym, payments=int(ok.sum()), mean_bps_per_payment=float(np.mean(r[ok]) * 1e4), annual_pct=ann))
    write_csv(os.path.join(a.out, "t2_units_check.csv"), units, ["exchange", "symbol", "payments", "mean_bps_per_payment", "annual_pct"])
    json.dump(dict(_banner=BANNER, selfcheck=sc, seconds=round(time.time() - t_start, 1), asset_pairs_sha256=sha256f(pairs_path),
                   missing_funding_files=sorted("%s/%s" % k for k in store.missing)[:50], n_missing=len(store.missing)),
              open(os.path.join(a.out, "t2_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("готово, секунд", round(time.time() - t_start, 1))


if __name__ == "__main__":
    sys.exit(main())
