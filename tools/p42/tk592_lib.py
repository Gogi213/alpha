"""П-42 §13.18 (TK-592): библиотека поиска пар — корзины, аддитивная модель, клетки, отсев BH, подтверждение (бутстреп по монетам и блокам суток, Холм).

Данные — таблица `ep_u0.parquet` (`tk592_build.py`): строка = независимый подход U0; столбцы: `side`, `sym`, `day`, `y` (O3 с возвратом глубже 0,1 %),
`c_f` (доля контроля), `c_n`, `in_pool`, 12 признаков. Превышение над контролем `d = y − c_f`; аддитивная модель — ридж на индикаторы корзин (без взаимодействий).
Всё, что зависит от данных (границы корзин, аддитивная модель), строится ТОЛЬКО на половине поиска и замораживается для половины подтверждения.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

SEED = 592
FEATS = ["pierce_ep", "spread_tau", "zv6h", "tpm60", "mv5", "cons60", "c5_btc_7d", "c5_eth_7d", "lturn24", "age_h", "natr_bp",
         "since_touch_prior_h"]
MIN_EP, MIN_POS = 300, 20
BH_Q = 0.10
HOLM_ALPHA = 0.05
SHORT_N = 10
B_BOOT = 2000
BLOCK = 4


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Признак `lturn24`, цель `d`; в анализ — эпизоды с контролем (c_n ≥ 5), сутки 44 (эмбарго) исключены."""
    d = df.copy()
    d["lturn24"] = np.log10(d["turn24h"].clip(lower=1))
    d = d[(d["c_n"] >= 5) & d["c_f"].notna() & (d["day"] != 44)].reset_index(drop=True)
    d["d"] = d["y"].astype(float) - d["c_f"].astype(float)
    return d


# ───────────────────────────── корзины ─────────────────────────────

class Buckets:
    """Корзины признака по квантилям половины поиска: 'tertile' (1/3, 2/3) или 'median'; совпавшие границы сливаются, пустая корзина отбрасывается."""

    def __init__(self, x_search: np.ndarray, scheme: str = "tertile"):
        v = np.asarray(x_search, float)
        v = v[~np.isnan(v)]
        qs = np.quantile(v, [1 / 3, 2 / 3] if scheme == "tertile" else [0.5]) if len(v) else np.array([])
        self.edges = np.unique(qs)
        codes = np.searchsorted(self.edges, v, "left")
        present = np.unique(codes)
        lut = np.full(len(self.edges) + 1, -1, int)
        for i, c in enumerate(present):
            lut[c] = i
        self.lut = lut
        self.nb = len(present)
        self.has_nan = bool(np.isnan(np.asarray(x_search, float)).any())

    def assign(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        out = np.full(len(x), -1, np.int64)
        ok = ~np.isnan(x)
        out[ok] = self.lut[np.searchsorted(self.edges, x[ok], "left")]
        return out

    def label(self, b: int) -> str:
        if self.nb == 3:
            return "LMH"[b]
        if self.nb == 2:
            return "LH"[b]
        return "L"


# ───────────────────────────── аддитивная модель ─────────────────────────────

class Additive:
    """Ридж-регрессия d на индикаторы корзин всех признаков (корзина L — базовая; индикатор пропуска), без взаимодействий. Строится на половине поиска."""

    def __init__(self, S: pd.DataFrame, feats=FEATS, lam: float = 1.0):
        self.feats = list(feats)
        self.b = {f: Buckets(S[f].to_numpy(), "tertile") for f in self.feats}
        X = self.design(S)
        n, p = X.shape
        Xc = np.c_[np.ones(n), X]
        pen = np.diag([0.0] + [lam] * p)
        self.beta = np.linalg.solve(Xc.T @ Xc + pen, Xc.T @ S["d"].to_numpy())

    def design(self, D: pd.DataFrame) -> np.ndarray:
        cols = []
        for f in self.feats:
            b = self.b[f]
            code = b.assign(D[f].to_numpy())
            for r in range(1, b.nb):
                cols.append((code == r).astype(float))
            if b.has_nan:
                cols.append((code == -1).astype(float))
        return np.column_stack(cols) if cols else np.zeros((len(D), 0))

    def predict(self, D: pd.DataFrame) -> np.ndarray:
        return self.beta[0] + self.design(D) @ self.beta[1:]


# ───────────────────────────── клетки ─────────────────────────────

def codes_for(D: pd.DataFrame, bk: dict) -> dict:
    return {key: b.assign(D[key[0]].to_numpy()) for key, b in bk.items()}


def make_buckets(S: pd.DataFrame, feats=FEATS) -> dict:
    return {(f, sc): Buckets(S[f].to_numpy(), sc) for f in feats for sc in ("tertile", "median")}


def cell_label(bk: dict, f: str, sc: str, b: int) -> str:
    return bk[(f, sc)].label(b)


def cell_id(side: str, f1: str, l1: str, f2: str, l2: str, sc: str) -> str:
    return f"{side}|{f1}={l1}|{f2}={l2}|{sc}"


def cluster_se(r: np.ndarray, mask: np.ndarray, codes: np.ndarray, ng: int) -> float:
    """Кластерно-робастная SE среднего r по клетке: sqrt(Σ_g (Σ_{i∈g}(r_i − r̄))²)/n."""
    n = int(mask.sum())
    if n < 2:
        return float("nan")
    rr = r[mask]
    s = np.bincount(codes[mask], weights=rr - rr.mean(), minlength=ng)
    return float(math.sqrt((s ** 2).sum()) / n)


def search_cells(S: pd.DataFrame, side: str, add: Additive, bk: dict, feats=FEATS):
    """Все читаемые клетки пар на половине поиска: список словарей с статистиками M, E1, SE, p."""
    r = (S["d"].to_numpy() - add.predict(S))
    y = S["y"].to_numpy()
    d = S["d"].to_numpy()
    cs = codes_for(S, bk)
    coin_codes, coins = pd.factorize(S["sym"])
    day_codes, days = pd.factorize(S["day"])
    first = S["day"].to_numpy() < (S["day"].min() + 15)  # первая 15-суточная часть половины
    cells = []
    for f1, f2 in itertools.combinations(feats, 2):
        chosen = None
        for sc in ("tertile", "median"):
            b1, b2 = bk[(f1, sc)], bk[(f2, sc)]
            if b1.nb < 2 or b2.nb < 2:
                continue
            c1, c2 = cs[(f1, sc)], cs[(f2, sc)]
            ok = (c1 >= 0) & (c2 >= 0)
            code = np.where(ok, c1 * 3 + c2, -1)
            counts = np.bincount(code[ok], minlength=9)
            pos = np.bincount(code[ok], weights=y[ok], minlength=9)
            present = counts > 0
            if sc == "tertile" and (pos[present] < MIN_POS).any():
                continue  # хоть одна клетка не читается по пробоям → медианное деление
            chosen = (sc, code, counts, pos)
            break
        if chosen is None:
            continue
        sc, code, counts, pos = chosen
        b1, b2 = bk[(f1, sc)], bk[(f2, sc)]
        for cc in np.flatnonzero(counts > 0):
            i1, i2 = divmod(int(cc), 3)
            if counts[cc] < MIN_EP or pos[cc] < MIN_POS:
                continue
            mask = code == cc
            M = float(r[mask].mean())
            se_c = cluster_se(r, mask, coin_codes, len(coins))
            se_d = cluster_se(r, mask, day_codes, len(days))
            se = max(se_c, se_d)
            z = M / se if se > 0 else 0.0
            m1, m2 = mask & first, mask & ~first
            cells.append({
                "id": cell_id(side, f1, b1.label(i1), f2, b2.label(i2), sc), "side": side, "f1": f1, "f2": f2, "scheme": sc, "b1": i1, "b2": i2,
                "l1": b1.label(i1), "l2": b2.label(i2), "n": int(counts[cc]), "k": int(pos[cc]),
                "y_share": float(y[mask].mean()), "c_share": float(S["c_f"].to_numpy()[mask].mean()), "E1": float(d[mask].mean()), "M": M,
                "se_coin": se_c, "se_day": se_d, "z": z, "p": float(2 * norm.sf(abs(z))),
                "M_s1": float(r[m1].mean()) if m1.any() else float("nan"), "M_s2": float(r[m2].mean()) if m2.any() else float("nan"),
                "n_s1": int(m1.sum()), "n_s2": int(m2.sum()),
            })
    return cells


def bh_adjust(ps: np.ndarray) -> np.ndarray:
    """Бенджамини—Хохберг: скорректированные p (q-значения)."""
    ps = np.asarray(ps, float)
    m = len(ps)
    if m == 0:
        return ps
    o = np.argsort(ps)
    q = ps[o] * m / (np.arange(m) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(m)
    out[o] = np.minimum(q, 1.0)
    return out


def holm_adjust(ps) -> np.ndarray:
    ps = np.asarray(ps, float)
    m = len(ps)
    if m == 0:
        return ps
    o = np.argsort(ps)
    adj = np.minimum(1.0, (m - np.arange(m)) * ps[o])
    adj = np.maximum.accumulate(adj)
    out = np.empty(m)
    out[o] = adj
    return out


def screen(cells: list) -> list:
    """Отсев на половине поиска: BH q = 0,10 по M, знак M одинаков в обеих 15-суточных частях, знак E1 = знаку M. Возвращает клетки с полями q, passed."""
    if not cells:
        return cells
    q = bh_adjust(np.array([c["p"] for c in cells]))
    for c, qq in zip(cells, q):
        sg = np.sign(c["M"])
        c["q"] = float(qq)
        c["passed"] = bool(qq < BH_Q and sg != 0 and np.sign(c["M_s1"]) == sg and np.sign(c["M_s2"]) == sg and np.sign(c["E1"]) == sg)
    return cells


def shortlist(cells: list, n: int = SHORT_N) -> list:
    ps = [c for c in cells if c.get("passed")]
    ps.sort(key=lambda c: (-abs(c["M"]), c["id"]))
    return ps[:n]


# ───────────────────────────── подтверждение ─────────────────────────────

def _agg_by(codes: np.ndarray, ng: int, mask: np.ndarray, r: np.ndarray):
    return (np.bincount(codes[mask], minlength=ng).astype(float), np.bincount(codes[mask], weights=r[mask], minlength=ng))


def block_weights(nd: int, B: int, rng: np.random.Generator, block: int = BLOCK) -> np.ndarray:
    """Веса суток в B пересэмплированиях скользящими блоками (как sigfeat_lib.block_resamples: nb блоков по `block` суток, усечение до nd суток)."""
    nb = math.ceil(nd / block)
    starts = rng.integers(0, max(nd - block, 0) + 1, size=(B, nb))
    sel = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(B, -1)[:, :nd]
    sel = np.minimum(sel, nd - 1)
    W = np.zeros((B, nd))
    for b in range(B):
        W[b] = np.bincount(sel[b], minlength=nd)
    return W


def boot_p0(vals: np.ndarray) -> float:
    vals = vals[~np.isnan(vals)]
    if len(vals) == 0:
        return float("nan")
    lo, hi = (vals <= 0).sum(), (vals >= 0).sum()
    return float(min(1.0, (2 * min(lo, hi) + 1) / (len(vals) + 1)))


def confirm_cells(T: pd.DataFrame, add: Additive, bk: dict, short: list, rng: np.random.Generator, B: int = B_BOOT) -> list:
    """Подтверждение шорт-листа на половине T с замороженными границами и аддитивной моделью. Статистика — M_T = среднее r клетки − среднее r по T."""
    out = []
    if not short:
        return out
    r = T["d"].to_numpy() - add.predict(T)
    d = T["d"].to_numpy()
    y = T["y"].to_numpy()
    cs = codes_for(T, bk)
    coin_codes, coins = pd.factorize(T["sym"])
    day_codes, days = pd.factorize(T["day"])
    # сутки по порядку (для блоков)
    rank = np.empty(len(days), int)
    rank[np.argsort(days.to_numpy())] = np.arange(len(days))
    day_rank = rank[day_codes]
    nc, nd = len(coins), len(days)
    delta = float(r.mean())
    Wc = rng.multinomial(nc, np.full(nc, 1 / nc), size=B).astype(float)
    Wd = block_weights(nd, B, rng)
    allc = _agg_by(coin_codes, nc, np.ones(len(T), bool), r)
    alld = _agg_by(day_rank, nd, np.ones(len(T), bool), r)
    in_pool = T["in_pool"].to_numpy().astype(bool)
    for c in short:
        f1, f2, sc = c["f1"], c["f2"], c["scheme"]
        b1, b2 = bk[(f1, sc)], bk[(f2, sc)]
        c1, c2 = cs[(f1, sc)], cs[(f2, sc)]
        mask = (c1 == c["b1"]) & (c2 == c["b2"])
        n, k = int(mask.sum()), int(y[mask].sum())
        row = {"id": c["id"], "side": c["side"], "n_T": n, "k_T": k}
        if n < 2:
            row.update({"M_T": float("nan"), "E1_T": float("nan"), "p_coin": 1.0, "p_day": 1.0, "p": 1.0})
            out.append(row)
            continue
        MT = float(r[mask].mean()) - delta
        row.update({"M_T": MT, "E1_T": float(d[mask].mean()), "y_share_T": float(y[mask].mean()), "c_share_T": float(T["c_f"].to_numpy()[mask].mean())})
        nC, sC = _agg_by(coin_codes, nc, mask, r)
        nD, sD = _agg_by(day_rank, nd, mask, r)
        with np.errstate(invalid="ignore", divide="ignore"):
            mc = (Wc @ sC) / (Wc @ nC) - (Wc @ allc[1]) / (Wc @ allc[0])
            md = (Wd @ sD) / (Wd @ nD) - (Wd @ alld[1]) / (Wd @ alld[0])
        row["p_coin"], row["p_day"] = boot_p0(mc), boot_p0(md)
        row["p"] = max(row["p_coin"], row["p_day"])
        row["ci_coin"] = [float(np.nanquantile(mc, 0.025)), float(np.nanquantile(mc, 0.975))]
        row["ci_day"] = [float(np.nanquantile(md, 0.025)), float(np.nanquantile(md, 0.975))]
        # без 5 монет с наибольшим числом пробоев в клетке
        pc = pd.Series(y[mask]).groupby(T["sym"].to_numpy()[mask]).sum().sort_values(ascending=False)
        top5 = set(pc.index[:5])
        keep = mask & ~T["sym"].isin(top5).to_numpy()
        row["M_T_no5"] = float(r[keep].mean()) - delta if keep.any() else float("nan")
        row["n_T_no5"] = int(keep.sum())
        # U1r — читаемое подмножество
        mu = mask & in_pool
        row["u1_n"], row["u1_k"] = int(mu.sum()), int(y[mu].sum())
        if mu.sum() > 0:
            row["u1_E1"] = float(d[mu].mean())
            row["u1_M"] = float(r[mu].mean()) - (float(r[in_pool].mean()) if in_pool.any() else 0.0)
        out.append(row)
    holm = holm_adjust([x["p"] for x in out])
    for x, h, c in zip(out, holm, short):
        sg = np.sign(c["M"])
        x["holm_p"] = float(h)
        x["sign_M"] = int(sg)
        x["confirmed"] = bool(h < HOLM_ALPHA and np.sign(x["M_T"]) == sg and np.sign(x.get("E1_T", 0.0)) == sg and np.sign(x.get("M_T_no5", 0.0)) == sg)
    return out
