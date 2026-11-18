"""П-42 §13.23 (TK-598): общая логика — срезы окон, строки сделок, метки целей, блоки CV с очисткой (purge), бутстреп по суткам, Холм, статистики S1 / S2.

Чистые функции без обращения к диску (кроме `read_detail`), поэтому тестируются на синтетике (`test_tk598.py`). Протокол — `docs/research/P-42-level-breakout.md`
§13.23 (п. 1 метки, п. 4 блоки и purge, п. 5 правило решения) и §13.23.1 (входы окон).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

DAY = 86_400_000
SEED = 598
B_BOOT = 20_000
BLOCK_DAYS = 4
HOLM_ALPHA = 0.05
MIN_SEL = 60                       # выбранных меньше 60 — цель «не определена» в окне
MIN_EVENTS = 30                    # цели с < 30 событий в F (T4: стопов) — «не определена»
MIN_ROWS_U0 = 3000                 # минимум строк U0-F на сторону
FEATS = ["pierce_ep", "spread_tau", "zv6h", "tpm60", "mv5", "cons60", "c5_btc_7d", "c5_eth_7d", "lturn24", "age_h", "natr_bp",
         "since_touch_prior_h"]    # = tk592_lib.FEATS (заморожен §13.18)
KEY = ["sym", "level", "sig_ms"]
# окно → (первые сутки, последние сутки включительно, число календарных суток в окне, T0 движка)
WINDOWS = {"F": (14, 73, 60, "2026-09-01"), "jul": (14, 44, 31, "2026-06-17"), "aug": (14, 44, 31, "2026-07-18"), "hold": (14, 16, 3, "2026-10-31")}
F_BLOCKS = [(14, 25), (26, 37), (38, 49), (50, 61), (62, 73)]      # 5 непрерывных блоков по 12 суток
GRID = [(d, m, l2) for d in (1, 2, 3) for m in (50, 200) for l2 in (3, 10)]   # 12 клеток: depth × min_data_in_leaf × l2_leaf_reg
TARGETS = {"long": ["T1", "T3", "T4", "T5", "T6"], "short": ["T1", "T2", "T3", "T4", "T5", "T6"]}   # T2 лонга ≡ T1 — не считается


# ───────────────────────────── сделки: detail и номинал ─────────────────────────────

def read_detail(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def nominal_usd(r) -> float:
    """Номинал сделки, $: Σ u ног лота 1 + Σ u ног лота 2 (если он есть), u — в мкУЕ (1e6); лонг = 500, шорт с `e2` — 250 + 250 или 250 при отменённом втором."""
    u = sum(int(L[2]) for L in r["legs"])
    lot2 = r.get("lot2")
    if lot2:
        u += sum(int(L[2]) for L in lot2["legs"])
    return u / 1e6


def trade_frame(trades_csv: str, detail_jsonl: str) -> pd.DataFrame:
    """Таблица сделок: trades.csv + номинал и NATR из detail.jsonl; ключ `(sym, level, sig_ms)` обязан быть уникальным, строки совпадают по числу."""
    tr = pd.read_csv(trades_csv)
    det = read_detail(detail_jsonl)
    d = pd.DataFrame({"sym": [r["sym"] for r in det], "level": [int(r["level"]) for r in det], "sig_ms": [int(r["sig_ms"]) for r in det],
                      "nominal": [nominal_usd(r) for r in det], "natr_e8": [int(r["natr_e8"]) for r in det],
                      "has_lot2": [bool(r.get("lot2")) for r in det]})
    if len(tr) != len(d):
        raise ValueError(f"trades.csv {len(tr)} строк, detail.jsonl {len(d)}")
    out = tr.merge(d, on=KEY, how="left", validate="one_to_one")
    if out["nominal"].isna().any():
        raise ValueError("строки trades.csv без записи detail.jsonl")
    if out.duplicated(KEY).any():
        raise ValueError("ключ (sym, level, sig_ms) не уникален")
    return out


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Метки целей: `net_nf = gross − fees` (без фандинга), `bps`, `y_win`, `y_stop`, сутки сигнала `day`."""
    d = df.copy()
    d["net_nf"] = d["gross"] - d["fees"]
    d["bps"] = d["net_nf"] / d["nominal"] * 1e4
    d["y_win"] = (d["net_nf"] > 0).astype(np.int8)
    d["y_stop"] = (d["kind"] == "stop").astype(np.int8)
    d["day"] = d["sig_ms"] // DAY
    return d


def window_mask(day, win: str):
    lo, hi, _, _ = WINDOWS[win]
    day = np.asarray(day)
    return (day >= lo) & (day <= hi)


# ───────────────────────────── блоки CV с очисткой ─────────────────────────────

def purge_train(day, entry_ms, exit_ms, block):
    """Индексы строк обучения для проверочного блока `block = (d0, d1)` (сутки сигнала включительно): строки вне блока, без соседних суток с обеих сторон
    (по 1) и без тех, чей интервал `[entry_ms, exit_ms]` пересекает блок, расширенный на 1 сутки (`[(d0 − 1)·DAY, (d1 + 2)·DAY)`)."""
    day = np.asarray(day)
    entry_ms = np.asarray(entry_ms)
    exit_ms = np.asarray(exit_ms)
    d0, d1 = block
    lo, hi = (d0 - 1) * DAY, (d1 + 2) * DAY
    outside = (day < d0 - 1) | (day > d1 + 1)
    overlap = (exit_ms >= lo) & (entry_ms < hi)
    return np.nonzero(outside & ~overlap)[0]


def block_of(day, blocks=F_BLOCKS):
    """Номер блока CV для суток сигнала; -1 вне блоков."""
    day = np.asarray(day)
    b = np.full(len(day), -1, np.int64)
    for i, (d0, d1) in enumerate(blocks):
        b[(day >= d0) & (day <= d1)] = i
    return b


# ───────────────────────────── метки по обучающей части ─────────────────────────────

def clip_bounds(y, lo_q=0.01, hi_q=0.99):
    y = np.asarray(y, float)
    y = y[~np.isnan(y)]
    return float(np.quantile(y, lo_q)), float(np.quantile(y, hi_q))


def quintile_bounds(y):
    """Границы квантилей 20 / 40 / 60 / 80 % по обучающей части (T6)."""
    y = np.asarray(y, float)
    return [float(q) for q in np.quantile(y[~np.isnan(y)], [0.2, 0.4, 0.6, 0.8])]


def quintile_rel(y, bounds):
    """Релевантность 0…4 = число границ строго ниже значения; ничья на границе — в один (нижний) бин: `y ≤ b₁` → 0."""
    y = np.asarray(y, float)
    return np.searchsorted(np.asarray(bounds), y, side="left").astype(np.int64)


def weight_norm(y_abs):
    """Вес T3: |net_nf|, нормированный на среднее по обучающей части (среднее = 1)."""
    w = np.asarray(y_abs, float)
    m = float(np.mean(w))
    return w / m if m > 0 else np.ones_like(w)


# ───────────────────────────── правило решения ─────────────────────────────

def pick_selected(s, q, lower=False):
    """Выбранные: `ŝ ≥ q` (T4 — `ŝ ≤ q`); NaN не выбирается."""
    s = np.asarray(s, float)
    with np.errstate(invalid="ignore"):
        return (s <= q) if lower else (s >= q)


def oof_threshold(s_oof, lower=False):
    """Граница «трети» по OOF-предсказаниям: `q_{2/3}` (T4 — `q_{1/3}`)."""
    s = np.asarray(s_oof, float)
    s = s[~np.isnan(s)]
    return float(np.quantile(s, 1 / 3 if lower else 2 / 3))


def day_series(day, net, sel, ndays, d_first):
    """Суточные ряды по окну: суммы и счётчики выбранных и всех (индекс суток от `d_first`, длина `ndays`)."""
    day = np.asarray(day, np.int64) - d_first
    net = np.asarray(net, float)
    sel = np.asarray(sel, bool)
    ok = (day >= 0) & (day < ndays)
    sa = np.bincount(day[ok], weights=net[ok], minlength=ndays)
    ca = np.bincount(day[ok], minlength=ndays).astype(float)
    ss = np.bincount(day[ok & sel], weights=net[ok & sel], minlength=ndays)
    cs = np.bincount(day[ok & sel], minlength=ndays).astype(float)
    return sa, ca, ss, cs


def point_stats(net, sel, ndays):
    """S1 = среднее выбранных − среднее всех; S2 = Σ выбранных / суток; S3 = Σ всех / суток; n_sel, n_all, mean_sel, mean_all."""
    net = np.asarray(net, float)
    sel = np.asarray(sel, bool)
    n_all, n_sel = len(net), int(sel.sum())
    mean_all = float(net.mean()) if n_all else float("nan")
    mean_sel = float(net[sel].mean()) if n_sel else float("nan")
    return {"n_all": n_all, "n_sel": n_sel, "mean_all": mean_all, "mean_sel": mean_sel,
            "S1": mean_sel - mean_all if n_sel and n_all else float("nan"),
            "S2": float(net[sel].sum() / ndays), "S3": float(net.sum() / ndays)}


def boot_indices(ndays, b=B_BOOT, block=BLOCK_DAYS, seed=SEED):
    """Блочный бутстреп по суткам: `b` × `ndays` индексов суток; блоки подряд идущих суток длиной `block` (скользящие, начало — равномерно из `[0, ndays − block]`),
    склеены и обрезаны до `ndays`. Детерминирован (`seed`)."""
    rng = np.random.default_rng(seed)
    block = min(block, ndays)
    nb = -(-ndays // block)
    starts = rng.integers(0, ndays - block + 1, size=(b, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(b, nb * block)[:, :ndays]
    return idx


def boot_stats(sa, ca, ss, cs, ndays, b=B_BOOT, seed=SEED, idx=None):
    """S1*, S2* по пересэмплированным суткам: S1* — отношение сумм (выбранные − все), S2* = Σ выбранных / ndays. Пустой набор выбранных → S1* = NaN."""
    if idx is None:
        idx = boot_indices(ndays, b, BLOCK_DAYS, seed)
    ssum, csum = ss[idx].sum(1), cs[idx].sum(1)
    asum, casum = sa[idx].sum(1), ca[idx].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        s1 = np.where(csum > 0, ssum / csum, np.nan) - np.where(casum > 0, asum / casum, np.nan)
    s2 = ssum / ndays
    return s1, s2


def p_one_sided(x, direction="le0"):
    """`p = (число + 1) / (B + 1)`: `le0` — P(x ≤ 0), `ge0` — P(x ≥ 0); NaN — считается нарушением (в числе)."""
    x = np.asarray(x, float)
    cnt = int(((x <= 0) | np.isnan(x)).sum()) if direction == "le0" else int(((x >= 0) | np.isnan(x)).sum())
    return (cnt + 1) / (len(x) + 1)


def holm_reject(pvals, alpha=HOLM_ALPHA):
    """Холм по `m` p-значениям: отвергнутые (список bool в исходном порядке); i-й по возрастанию p сравнивается с `alpha / (m − i + 1)`, остановка на первом отказе."""
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p, kind="stable")
    rej = np.zeros(m, bool)
    for rank, i in enumerate(order):
        if p[i] <= alpha / (m - rank):
            rej[i] = True
        else:
            break
    return rej.tolist()


def drop_top_sum(net, sel, keys, n_drop=2):
    """Σ net выбранных без `n_drop` лучших групп (суток или монет) по сумме net выбранных в группе."""
    net = np.asarray(net, float)[np.asarray(sel, bool)]
    k = np.asarray(keys)[np.asarray(sel, bool)]
    if len(net) == 0:
        return 0.0
    g = pd.Series(net).groupby(k).sum().sort_values(ascending=False)
    return float(net.sum() - g.iloc[:n_drop].sum())


def mdl_lift(sigma, n_sel, m=1):
    """Наименьший различимый лифт `(z_{1−α/m} + 0,84)·σ/√n_sel` (одностороннее 5 %, мощность 80 %); `m = 1` — до Холма."""
    from scipy.stats import norm
    z = norm.ppf(1 - HOLM_ALPHA / m)
    return float((z + 0.84) * sigma / math.sqrt(n_sel)) if n_sel > 0 else float("nan")


def auc(y, s):
    """AUC (ранговая, средние ранги при ничьих); NaN, если один класс."""
    from scipy.stats import rankdata
    y = np.asarray(y).astype(bool)
    s = np.asarray(s, float)
    ok = ~np.isnan(s)
    y, s = y[ok], s[ok]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def spearman(a, b):
    from scipy.stats import spearmanr
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 3:
        return float("nan")
    r = spearmanr(a[ok], b[ok]).statistic
    return float(r)
