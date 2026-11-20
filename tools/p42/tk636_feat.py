#!/usr/bin/env python3
"""П-42 §13.35 (TK-636): признаки монеты на кандидатах баз — свойства контракта, оборот / возраст / NATR, связь с BTC / ETH / кластером в окнах внутри суток, одновременные пробои соседей по кластеру. P&L НЕ читает.
  python3 tk636_feat.py build --uni h775|o566      — таблицы /data/p42-tk636/feat/<вс>/<окно>-<сторона>.csv + ворота G-eq (сверка с готовыми c5_* f12) + info.json
  python3 tk636_feat.py g3x --uni h775|o566 [--k 120]   — ворота G3x: значение на кандидате = перебору, мусор после `t` (и после дня обновления кластеров) признак не меняет
Причинность: только закрытые бары строго раньше минуты сигнала t = sig_ms // 60000 (как `sigfeat_run.corr_features`); кластеры месяца — по 5-мин доходностям 14 суток ДО 00:00 UTC первого дня месяца."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = "/data/p42-tk636"
FEAT34 = "/data/p42-tk634/feat"
CONTRACTS = "/data/p42-tk621/universe/contracts.csv"
EV_DIR = {"h775": "/data/p42-tk625/ev-h775", "o566": "/data/p42-tk625/ev"}
MIN_MS = 60_000
DAY_MS = 86_400_000
SIDE_NAME = {"L": "long", "S": "short"}
# окна: (шаг доходностей, число баров, минимум общих пар) — п. 4 §13.35
WIN_SPEC = {"15m": ("r1", 15, 10), "1h": ("r1", 60, 40), "4h": ("r5", 48, 30), "24h": ("r5", 288, 100), "7d": ("r5", 2016, 700)}
WIN_ORDER = ("15m", "1h", "4h", "24h", "7d")
TRIAL_WINS = ("15m", "1h", "4h", "24h")                  # 7d — только справка
COMP = ("cb", "ce", "bt", "cc")
CLU_DAYS, CLU_MIN_PAIRS, CLU_CUT, CLU_MIN_SIZE, CLU_MIN_OBS = 14, 2000, 0.3, 3, 100
PEER_W = (("peer5", 5), ("peer15", 15), ("peer60", 60))
PEERALL_W = 15
REFRESH_WINDOW = {7: "wa", 8: "wa", 9: "wb", 10: "wb", 11: "wc"}     # месяц → окно, чей кэш покрывает 14 суток до обновления (wa — данные до 29.08, поэтому 01.09 — по wb)
REFS = ("BTCUSDT", "ETHUSDT")
OUT_COLS = (["cls", "turn24h", "natr_bp", "age_d", "clu_id", "clu_n"] + [n for n, _ in PEER_W] + ["peerall15"] + ["%s_%s" % (c, w) for w in WIN_ORDER for c in COMP])
GEQ_PAIRS = (("cb_24h", "c5_btc_24h"), ("ce_24h", "c5_eth_24h"), ("cb_7d", "c5_btc_7d"), ("ce_7d", "c5_eth_7d"))
GEQ_TOL = 1e-9


# ---- минутки и доходности ----------------------------------------------------------------------------------------------------------

def load_b42(path):
    """Кэш минуток `.b42` движка: (o, c) — открытие (0 = минутка без данных) и закрытие, int64 длиной N минут окна."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] != b"B42\x01":
        raise ValueError("%s: не .b42" % path)
    n = int.from_bytes(head[4:8], "little")
    a = np.fromfile(path, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
    return a[0], a[3]


def ret1(o, c):
    """Минутные доходности: c[i] / c[i−1] − 1; NaN, если у любой из двух минуток нет данных."""
    n = len(c)
    cf = c.astype(np.float64)
    r = np.full(n, np.nan)
    ok = (o[1:] != 0) & (o[:-1] != 0)
    r[1:][ok] = cf[1:][ok] / cf[:-1][ok] - 1.0
    return r


def ret5(o, c, k=5):
    """Доходности блоков k минут (границы по UTC от T0): закрытие блока — последнее закрытие внутри; блок без данных или сосед без данных — NaN (как `sigfeat_lib.block_returns`)."""
    n = len(c)
    nb = n // k
    ob = o[: nb * k].reshape(nb, k)
    cb = c[: nb * k].reshape(nb, k)
    valid = (ob != 0).any(axis=1)
    last = k - 1 - np.argmax((ob != 0)[:, ::-1], axis=1)
    close = np.where(valid, cb[np.arange(nb), last].astype(np.float64), np.nan)
    r = np.full(nb, np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    return r


def last_bar(kind, t):
    """Индекс последнего закрытого бара до минуты сигнала t: 1-мин — t − 1, 5-мин блок — t // 5 − 1."""
    return t - 1 if kind == "r1" else t // 5 - 1


def wstat(x, y, last, n, min_n):
    """(корреляция Пирсона, бета x к y = cov / var(y)) по барам [last − n + 1, last] (не раньше 0), общие конечные пары; меньше min_n пар — (NaN, NaN)."""
    a = max(last - n + 1, 0)
    if last < a or last >= len(x):
        return float("nan"), float("nan")
    xs, ys = x[a:last + 1], y[a:last + 1]
    ok = np.isfinite(xs) & np.isfinite(ys)
    if int(ok.sum()) < min_n:
        return float("nan"), float("nan")
    xa, ya = xs[ok], ys[ok]
    xm, ym = xa - xa.mean(), ya - ya.mean()
    sxx, syy, sxy = float((xm * xm).sum()), float((ym * ym).sum()), float((xm * ym).sum())
    corr = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")
    beta = sxy / syy if syy > 0 else float("nan")
    return corr, beta


def mate_mean(R, mates, a, b):
    """Равновзвешенное среднее доходностей соседей по барам [a, b): бар определён при ≥ 2 соседях с доходностью, иначе NaN."""
    sub = R[mates, a:b]
    fin = np.isfinite(sub)
    cnt = fin.sum(axis=0)
    s = np.where(fin, sub, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt >= 2, s / np.where(cnt > 0, cnt, 1), np.nan)


def corr_features(R1, R5, ci, bi, ei, mates, t, self_sym):
    """Компоненты cb / ce / bt / cc во всех окнах на кандидате: dict '<comp>_<окно>' → float. R1, R5 — матрицы доходностей (монеты × бары), ci / bi / ei — строки монеты, BTC, ETH; mates — строки соседей (пусто — нет кластера)."""
    out = {}
    for w in WIN_ORDER:
        kind, n, mn = WIN_SPEC[w]
        R = R1 if kind == "r1" else R5
        last = last_bar(kind, t)
        a = max(last - n + 1, 0)
        for comp, ref_i, ref_name in (("cb", bi, REFS[0]), ("ce", ei, REFS[1])):
            if ci is None or ref_i is None or self_sym == ref_name:
                out["%s_%s" % (comp, w)] = float("nan")
                if comp == "cb":
                    out["bt_%s" % w] = float("nan")
                continue
            c, b = wstat(R[ci], R[ref_i], last, n, mn)
            out["%s_%s" % (comp, w)] = c
            if comp == "cb":
                out["bt_%s" % w] = b
        if ci is not None and len(mates) >= 2 and last >= a:
            mm = mate_mean(R, mates, a, last + 1)
            full = np.full(last + 1, np.nan)
            full[a:last + 1] = mm
            out["cc_%s" % w] = wstat(R[ci], full, last, n, mn)[0]
        else:
            out["cc_%s" % w] = float("nan")
    return out


# ---- причинные кластеры ------------------------------------------------------------------------------------------------------------

def residualize(X, btc):
    """Остатки после беты к BTC (МНК по общим блокам, ≥ CLU_MIN_OBS); строка без достаточных данных — NaN."""
    ok = np.isfinite(X) & np.isfinite(btc)[None, :]
    E = np.full_like(X, np.nan)
    for i in range(X.shape[0]):
        o = ok[i]
        if int(o.sum()) < CLU_MIN_OBS:
            continue
        x, y = btc[o], X[i, o]
        xm = x - x.mean()
        v = float((xm * xm).sum())
        beta = float((xm * (y - y.mean())).sum() / v) if v > 0 else float("nan")
        if beta == beta:
            E[i, o] = y - beta * x
    return E


def corr_matrix(R, min_n):
    """Корреляции строк R по попарно полным блокам (суммы через произведения масок, как `sigfeat_lib.corr_matrix`); пар меньше min_n — NaN; диагональ 1."""
    M = np.isfinite(R).astype(np.float64)
    X = np.nan_to_num(R, nan=0.0)
    n = M @ M.T
    sx = X @ M.T
    sxx = (X * X) @ M.T
    sxy = X @ X.T
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = n * sxy - sx * sx.T
        vx = n * sxx - sx * sx
        r = cov / np.sqrt(vx * vx.T)
    r[n < min_n] = np.nan
    np.fill_diagonal(r, 1.0)
    return r


def cluster_labels(R5, btc_i, eth_i, a, b, cut=CLU_CUT, min_pairs=CLU_MIN_PAIRS, min_size=CLU_MIN_SIZE):
    """Метки кластеров монет по 5-мин блокам [a, b): остатки после беты к BTC, корреляция остатков, средняя связь по 1 − ρ, срез на средней корреляции ≥ cut; кластер < min_size монет и BTC / ETH — метка −1.
    Читает ТОЛЬКО R5[:, a:b] (мусор вне диапазона результат не меняет — G3x)."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    X = R5[:, a:b]
    E = residualize(X, X[btc_i])
    for i in (btc_i, eth_i):
        if i is not None:
            E[i, :] = np.nan
    r = corr_matrix(E, min_pairs)
    n = r.shape[0]
    d = 1.0 - np.where(np.isnan(r), 0.0, r)
    np.fill_diagonal(d, 0.0)
    d = (d + d.T) / 2.0
    z = linkage(squareform(d, checks=False), method="average")
    lab = fcluster(z, t=1.0 - cut, criterion="distance")
    out = np.full(n, -1, dtype=np.int64)
    for c in np.unique(lab):
        idx = np.flatnonzero(lab == c)
        if len(idx) >= min_size and not any(i in (btc_i, eth_i) for i in idx):
            out[idx] = int(c)
    return out


def refresh_blocks(t0_date, month_first):
    """Диапазон 5-мин блоков [a, b) окна: 14 суток до 00:00 UTC первого дня месяца (b — блок начала месяца)."""
    b = (month_first - t0_date).days * 288
    return b - CLU_DAYS * 288, b


# ---- события-пересечения и соседи ------------------------------------------------------------------------------------------------

class Events:
    """Пересечения TK-625 одной стороны окна: время `cross_ms` (мс от T0) по монетам; исход события не используется."""

    def __init__(self, syms, cross_ms):
        self.by_sym = {}
        s = np.asarray(syms, dtype=object)
        c = np.asarray(cross_ms, dtype=np.int64)
        o = np.argsort(c, kind="stable")
        self.all_t, self.all_s = c[o], s[o]
        for sym in np.unique(s):
            self.by_sym[sym] = np.sort(c[s == sym])

    def count(self, mates, t_ms, w_min):
        """Число различных монет из `mates` (имена) с событием в [t_ms − w, t_ms)."""
        lo, hi = t_ms - w_min * MIN_MS, t_ms
        k = 0
        for m in mates:
            a = self.by_sym.get(m)
            if a is None:
                continue
            i = int(np.searchsorted(a, lo, side="left"))
            if i < len(a) and a[i] < hi:
                k += 1
        return k

    def count_all(self, sym, t_ms, w_min):
        """Число различных монет (кроме `sym`) с событием в [t_ms − w, t_ms) по всей вселенной."""
        i = int(np.searchsorted(self.all_t, t_ms - w_min * MIN_MS, side="left"))
        j = int(np.searchsorted(self.all_t, t_ms, side="left"))
        return len({s for s in self.all_s[i:j] if s != sym})


def load_events_df(uni, window, side):
    """События стороны с записанным пересечением: (sym, cross_ms). Условие `ok_ev` НЕ используется (п. 4 §13.35: ok_ev смотрит на сутки после `cross_ms`); `cross_ms` пишется движком событий только при `ok_ev = 1` — события без записи пересечения (NATR ≤ 0, конец сетки) отсутствуют."""
    import pandas as pd
    d = pd.read_csv(os.path.join(EV_DIR[uni], window + ".csv"), usecols=["side", "sym", "cross_ms"])
    d = d[(d["side"] == SIDE_NAME[side]) & d["cross_ms"].notna()]
    return d[["sym", "cross_ms"]].reset_index(drop=True)


def events_of(d):
    return Events(d["sym"].to_numpy(dtype=object), d["cross_ms"].to_numpy(dtype=np.float64).astype(np.int64))


def load_events(uni, window, side):
    return events_of(load_events_df(uni, window, side))


# ---- контракты и возраст -------------------------------------------------------------------------------------------------------------

def read_contracts(path=CONTRACTS):
    import csv
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]] = r
    return out


def age_days(abs_ms, onboard):
    """Возраст листинга в сутках на дату сигнала (UTC): дата сигнала − onboard_date, < 0 → 0; нет даты — NaN."""
    if not onboard:
        return float("nan")
    d = datetime.datetime.fromtimestamp(abs_ms / 1000.0, tz=datetime.timezone.utc).date()
    return float(max((d - datetime.date.fromisoformat(onboard)).days, 0))


def month_of(abs_ms):
    d = datetime.datetime.fromtimestamp(abs_ms / 1000.0, tz=datetime.timezone.utc)
    return d.month if d.year == 2026 else 0


# ---- сборка ------------------------------------------------------------------------------------------------------------------------------

def fmt(x):
    x = float(x)
    return "" if x != x else repr(x)


class Win:
    """Матрицы доходностей окна для монет вселенной (строка — монета из `coins`; нет кэша — строка из NaN)."""

    def __init__(self, tree_dir, coins):
        self.coins = list(coins)
        self.ix = {s: i for i, s in enumerate(self.coins)}
        self.R1 = self.R5 = None
        self.have = np.zeros(len(self.coins), dtype=bool)
        for i, s in enumerate(self.coins):
            p = os.path.join(tree_dir, "cache", s + ".b42")
            if not os.path.exists(p):
                continue
            o, c = load_b42(p)
            if self.R1 is None:
                self.R1 = np.full((len(self.coins), len(c)), np.nan)
                self.R5 = np.full((len(self.coins), len(c) // 5), np.nan)
            self.R1[i] = ret1(o, c)
            self.R5[i] = ret5(o, c)
            self.have[i] = True
        if self.R1 is None:
            raise RuntimeError("в %s нет ни одного кэша" % tree_dir)


def month_clusters(wobj, t0_date, month):
    """Метки кластеров месяца по 14 суткам до его начала — из матриц окна `wobj` (`month_clusters` читает только эти блоки)."""
    first = datetime.date(2026, month, 1)
    a, b = refresh_blocks(t0_date, first)
    if a < 0 or b > wobj.R5.shape[1]:
        raise RuntimeError("месяц %d: 14 суток до обновления вне кэша окна (блоки %d…%d из %d)" % (month, a, b, wobj.R5.shape[1]))
    bi, ei = wobj.ix.get(REFS[0]), wobj.ix.get(REFS[1])
    return cluster_labels(wobj.R5, bi, ei, a, b)


def row_values(wobj, labels_by_month, ev, sym, t_ms, abs_ms, contract, mates_cache=None):
    """Строка признаков кандидата (sym, t_ms от T0 окна): все столбцы OUT_COLS (NaN — нет значения)."""
    t = t_ms // MIN_MS
    ci = wobj.ix.get(sym) if (sym in wobj.ix and wobj.have[wobj.ix[sym]]) else None
    bi, ei = wobj.ix.get(REFS[0]), wobj.ix.get(REFS[1])
    mon = month_of(abs_ms)
    lab = labels_by_month.get(mon)
    mates_idx, mate_names, clu_id, clu_n = np.zeros(0, dtype=np.int64), [], float("nan"), float("nan")
    if lab is not None and ci is not None and lab[ci] >= 0:
        members = np.flatnonzero(lab == lab[ci])
        mates_idx = members[members != ci]
        mate_names = [wobj.coins[i] for i in mates_idx]
        clu_id, clu_n = float(lab[ci]), float(len(members))
    row = {"cls": float(contract["tradfi"]) if contract else float("nan"), "age_d": age_days(abs_ms, contract["onboard_date"] if contract else ""), "clu_id": clu_id, "clu_n": clu_n}
    for name, w in PEER_W:
        row[name] = float(ev.count(mate_names, t_ms, w)) if mate_names else float("nan")
    row["peerall15"] = float(ev.count_all(sym, t_ms, PEERALL_W))
    row.update(corr_features(wobj.R1, wobj.R5, ci, bi, ei, mates_idx, t, sym))
    return row


def cmd_build(a):
    import pandas as pd
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni(a.uni)
    contracts = read_contracts()
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    labels_by_month, cluster_info = {}, {}
    geq = {}
    for w in st.TRADE_WINDOWS:
        tree = u.wdir(w.name)
        t0d = w.t0d
        print("окно %s: загрузка кэшей %s" % (w.name, tree), flush=True)
        wobj = Win(tree, u.coins())
        for mon in sorted(m for m, wn in REFRESH_WINDOW.items() if wn == w.name):
            lab = month_clusters(wobj, t0d, mon)
            labels_by_month[mon] = (lab, list(wobj.coins))
            sizes = sorted((int((lab == c).sum()) for c in np.unique(lab) if c >= 0), reverse=True)
            cluster_info[mon] = dict(window=w.name, clusters=len(sizes), sizes=sizes, coins_in_cluster=int((lab >= 0).sum()))
            print("  месяц %02d: кластеров %d, в кластерах %d монет, размеры %s" % (mon, len(sizes), int((lab >= 0).sum()), sizes[:12]), flush=True)
        # метки месяцев — по списку монет вселенной (один порядок во всех окнах)
        lbm = {m: lab for m, (lab, _) in labels_by_month.items()}
        for side in ("L", "S"):
            src = os.path.join(FEAT34, u.name, "%s-%s.csv" % (w.name, side))
            tab = pd.read_csv(src, usecols=["sym", "sig_ms", "turn24h", "natr_bp"])
            eps = pd.read_parquet(os.path.join(FEAT34, u.name, "%s-%s" % (w.name, side), "eps-%s.parquet" % side), columns=["sym", "sig_ms"] + [g[1] for g in GEQ_PAIRS])
            ev = load_events(u.name, w.name, side)
            rows = []
            for sym, sig_ms in zip(tab["sym"], tab["sig_ms"]):
                abs_ms = w.t0_ms + int(sig_ms)
                st.check_abs_ms([abs_ms], "кандидат")
                rows.append(row_values(wobj, lbm, ev, sym, int(sig_ms), abs_ms, contracts.get(sym)))
            feat = pd.DataFrame(rows)
            feat["turn24h"] = tab["turn24h"].to_numpy()
            feat["natr_bp"] = tab["natr_bp"].to_numpy()
            # G-eq: 24 ч и 7 сут. корреляции с BTC / ETH = готовым c5_* f12
            eq = pd.DataFrame({"sym": tab["sym"], "sig_ms": tab["sig_ms"]})
            for mine, old in GEQ_PAIRS:
                eq[mine] = feat[mine].to_numpy()
            mg = eq.merge(eps, on=["sym", "sig_ms"], how="left", validate="one_to_one")
            for mine, old in GEQ_PAIRS:
                x, y = mg[mine].to_numpy(), mg[old].to_numpy()
                both = np.isfinite(x) & np.isfinite(y)
                geq["%s-%s/%s" % (w.name, side, mine)] = dict(rows=int(len(x)), both=int(both.sum()), only_mine=int((np.isfinite(x) & ~np.isfinite(y)).sum()), only_old=int((~np.isfinite(x) & np.isfinite(y)).sum()),
                                                              max_abs=float(np.abs(x[both] - y[both]).max()) if both.any() else 0.0)
            out_dir = os.path.join(ROOT, "feat", u.name)
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "%s-%s.csv" % (w.name, side))
            order = sorted(range(len(tab)), key=lambda i: (tab["sym"].iloc[i], int(tab["sig_ms"].iloc[i])))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(OUT_COLS) + "\n")
                for i in order:
                    f.write("%s,%d,%s\n" % (tab["sym"].iloc[i], int(tab["sig_ms"].iloc[i]), ",".join(fmt(feat[c].iloc[i]) for c in OUT_COLS)))
            print("  %s-%s: %d строк → %s" % (w.name, side, len(tab), path), flush=True)
        del wobj
    bad = {k: v for k, v in geq.items() if v["max_abs"] > GEQ_TOL or v["only_mine"] or v["only_old"]}
    info = dict(uni=u.name, geq=geq, geq_bad=bad, clusters=cluster_info,
                sha256={f: hashlib.sha256(open(os.path.join(ROOT, "feat", u.name, f), "rb").read()).hexdigest() for f in sorted(os.listdir(os.path.join(ROOT, "feat", u.name))) if f.endswith(".csv")})
    json.dump(info, open(os.path.join(ROOT, "feat", u.name, "info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-eq: %s" % ("FAIL %s" % json.dumps(bad, ensure_ascii=False)[:1500] if bad else "ok: %d сверок, макс. |Δ| %.2e" % (len(geq), max(v["max_abs"] for v in geq.values()))), flush=True)
    if bad:
        sys.exit(1)
    # метки кластеров месяцев — отдельным файлом (для counts и отчёта)
    for mon, (lab, coins) in labels_by_month.items():
        with open(os.path.join(ROOT, "feat", u.name, "clusters-%02d.csv" % mon), "w", encoding="utf-8", newline="\n") as f:
            f.write("sym,cluster\n")
            for s, l in zip(coins, lab):
                if l >= 0:
                    f.write("%s,%d\n" % (s, l))


def cmd_g3x(a):
    """G3x: на k случайных кандидатах вселенной значение признаков = значению на матрицах, где данные ≥ t (1-мин и блоки, содержащие t) и кластеров — данные ≥ дня обновления заменены мусором."""
    import pandas as pd
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni(a.uni)
    contracts = read_contracts()
    rng = np.random.default_rng(636)
    bad, checked = [], 0
    labels_by_month = {}
    for w in st.TRADE_WINDOWS:
        wobj = Win(u.wdir(w.name), u.coins())
        for mon in sorted(m for m, wn in REFRESH_WINDOW.items() if wn == w.name):
            lab = month_clusters(wobj, w.t0d, mon)
            labels_by_month[mon] = lab
            # кластеры: мусор после дня обновления
            a_, b_ = refresh_blocks(w.t0d, datetime.date(2026, mon, 1))
            junk = wobj.R5.copy()
            junk[:, b_:] = rng.normal(0, 0.01, junk[:, b_:].shape)
            if not np.array_equal(cluster_labels(junk, wobj.ix[REFS[0]], wobj.ix[REFS[1]], a_, b_), lab):
                bad.append("clusters %02d: метки изменились от мусора после дня обновления" % mon)
        for side in ("L", "S"):
            tab = pd.read_csv(os.path.join(ROOT, "feat", u.name, "%s-%s.csv" % (w.name, side)))
            evd = load_events_df(u.name, w.name, side)
            ev = events_of(evd)
            all_syms = np.asarray(evd["sym"].unique(), dtype=object)
            pick = rng.choice(len(tab), size=min(a.k // 6 + 1, len(tab)), replace=False)
            for i in pick:
                sym, sig_ms = tab["sym"].iloc[i], int(tab["sig_ms"].iloc[i])
                abs_ms = w.t0_ms + sig_ms
                base = row_values(wobj, labels_by_month, ev, sym, sig_ms, abs_ms, contracts.get(sym))
                t = sig_ms // MIN_MS
                saved = (wobj.R1, wobj.R5)
                wobj.R1, wobj.R5 = saved[0].copy(), saved[1].copy()
                wobj.R1[:, t:] = rng.normal(0, 0.01, wobj.R1[:, t:].shape)
                wobj.R5[:, t // 5:] = rng.normal(0, 0.01, wobj.R5[:, t // 5:].shape)
                trash = row_values(wobj, labels_by_month, ev, sym, sig_ms, abs_ms, contracts.get(sym))
                wobj.R1, wobj.R5 = saved
                for c in COMP_COLS():
                    x, y = base[c], trash[c]
                    if not ((x != x and y != y) or x == y):
                        bad.append("%s %s %d: %s %r ≠ %r (мусор после t)" % (w.name, sym, sig_ms, c, x, y))
                # события: все с cross_ms >= t заменены мусором (сдвиг в [t, t + 6 ч)), плюс 20 лишних в [t, t + 1 ч) — счёт соседей на [t − W, t) не меняется
                cm = evd["cross_ms"].to_numpy(dtype=np.float64).astype(np.int64)
                late = cm >= sig_ms
                cm2 = cm.copy()
                cm2[late] = sig_ms + rng.integers(0, 6 * 60 * MIN_MS, int(late.sum()))
                extra_s = rng.choice(all_syms, 20)
                extra_t = sig_ms + rng.integers(0, 60 * MIN_MS, 20)
                ev2 = Events(np.concatenate([evd["sym"].to_numpy(dtype=object), extra_s]), np.concatenate([cm2, extra_t]).astype(np.int64))
                tr2 = row_values(wobj, labels_by_month, ev2, sym, sig_ms, abs_ms, contracts.get(sym))
                for c in [n for n, _ in PEER_W] + ["peerall15"]:
                    x, y = base[c], tr2[c]
                    if not ((x != x and y != y) or x == y):
                        bad.append("%s %s %d: %s %r ≠ %r (события ≥ t)" % (w.name, sym, sig_ms, c, x, y))
                checked += 1
        del wobj
    res = dict(uni=u.name, checked=checked, bad=bad)
    json.dump(res, open(os.path.join(ROOT, "gates", "g3x-%s.json" % u.name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G3x %s: %s" % (u.name, ("FAIL %s" % bad[:5]) if bad else "ok, кандидатов %d, мусор после t признаки не меняет" % checked), flush=True)
    if bad:
        sys.exit(1)


def COMP_COLS():
    return ["%s_%s" % (c, w) for w in WIN_ORDER for c in COMP]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["build", "g3x"])
    ap.add_argument("--uni", required=True, choices=("h775", "o566"))
    ap.add_argument("--k", type=int, default=120)
    a = ap.parse_args()
    os.makedirs(os.path.join(ROOT, "gates"), exist_ok=True)
    {"build": cmd_build, "g3x": cmd_g3x}[a.cmd](a)


if __name__ == "__main__":
    main()
