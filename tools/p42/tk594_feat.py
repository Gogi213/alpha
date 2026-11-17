"""П-42 §13.20 (TK-594): признаки «поток сделок» (16) и «производные метрики» (14) для таблицы эпизодов.

Использование (srv): python3 tk594_feat.py <episodes.parquet> <out.parquet> [--procs 6] [--coins A,B] [--base-date 2026-09-01]
Читает из эпизодов только ключи (`ep_id`, `sym`, `side`, `sig_ms`, `day`) — столбцы меток и контролей не открываются. `sig_ms` — мс от `--base-date` (как у движка).
Источники (каталоги — переменные окружения): aggTrades `TK594_TRADES` (список через `:`; `<корень>/<SYM>/<SYM>-aggTrades-<день>.zip`), `metrics` `TK594_METRICS`,
`premiumIndexKlines` 1m `TK594_PREMIUM`, фандинг `TK594_FUNDING_CSV` (`<корень>/<SYM>/funding.csv`, с 01.09) и `TK594_FUNDING_M` (месячные архивы `fundingRate`).
Определения — docs/research/P-42-level-breakout.md §13.20 п. 2; окна потока — строго раньше `sig_ms`; строка `metrics` — только `T* = ⌊(sig_ms − 10 мин)/5 мин⌋·5 мин`.
Уточнения реализации (до чтения меток): граница корзины `vpin` — запись, на которой накопленный оборот впервые ≥ k/50·(1 − 1e-12) общего, эта запись замыкает корзину;
`tfp300` пуст и без 24 ч записей монеты (как `vpin`); интервал фандинга — по разности `funding_time` двух последних расчётов.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import zipfile
from multiprocessing import Pool

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MIN_MS = 60_000
NB = 1200  # гистограмма log10 v: 1e-3 … 1e9, шаг 0,01 декады
EDGES = [10.0 ** ((j - 300) / 100.0) for j in range(NB + 1)]  # единые границы корзин для быстрой и независимой реализаций
EDGES_ARR = np.array(EDGES)
VPIN_TOL = 1e-12
FLOW = ["tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "big300", "big1800", "tfp300", "lmsz300", "msz_r",
        "vpin"]
DERIV = ["oi_c1", "oi_c4", "oi_c24", "loi_turn", "ls_tc", "ls_ts", "ls_a", "dls_tc", "dls_ts", "dls_a", "fund_last", "fund_chg24", "fund_int", "basis"]
WIN_S = (10, 60, 300, 1800)

TRADES = os.environ.get("TK594_TRADES", "/data/aggtrades/binance").split(":")
METRICS = os.environ.get("TK594_METRICS", "/data/p42-metrics")
PREMIUM = os.environ.get("TK594_PREMIUM", "/data/p42-premium")
FUND_CSV = os.environ.get("TK594_FUNDING_CSV", "/data/klines/binance")
FUND_M = os.environ.get("TK594_FUNDING_M", "/data/p42-metrics/funding")


def date_str(abs_day: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(abs_day))).isoformat()


def base_ms(base_date: str) -> int:
    y, m, d = map(int, base_date.split("-"))
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ───────────────────────────── чтение ─────────────────────────────

def read_trades_day(sym: str, abs_day: int):
    """Записи суток: (t мс, v = price·qty, покупка тейкера); None — файла нет. Порядок как в файле; при убывании времени — устойчивая сортировка."""
    name = f"{sym}-aggTrades-{date_str(abs_day)}"
    for root in TRADES:
        path = f"{root}/{sym}/{name}.zip"
        if not os.path.exists(path):
            continue
        with zipfile.ZipFile(path) as zf, zf.open(name + ".csv") as fh:
            df = pd.read_csv(fh, usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
                             dtype={"price": np.float64, "quantity": np.float64, "transact_time": np.int64})
        t = df["transact_time"].to_numpy()
        v = df["price"].to_numpy() * df["quantity"].to_numpy()
        col = df["is_buyer_maker"]
        maker = col.to_numpy() if col.dtype == bool else col.astype(str).str.lower().eq("true").to_numpy()
        buy = ~maker
        if len(t) > 1 and np.any(np.diff(t) < 0):
            o = np.argsort(t, kind="stable")
            t, v, buy = t[o], v[o], buy[o]
        return t, v, buy
    return None


def read_zip_csv(path: str, inner: str, usecols):
    with zipfile.ZipFile(path) as zf, zf.open(inner) as fh:
        return pd.read_csv(fh, usecols=usecols)


# ───────────────────────────── поток ─────────────────────────────

def win_stats(T, V, B, i_lo: int, i_hi: int):
    """(оборот, оборот покупок, число записей, число покупок) по срезу [i_lo, i_hi)."""
    if i_hi <= i_lo:
        return 0.0, 0.0, 0, 0
    v = V[i_lo:i_hi]
    b = B[i_lo:i_hi]
    return float(v.sum()), float(v[b].sum()), int(i_hi - i_lo), int(b.sum())


def vpin_value(V, B, lo: int, hi: int):
    """50 корзин равного оборота на записях [lo, hi); граница — запись, на которой накопленный оборот впервые ≥ k/50·(1 − 1e-12) общего (замыкает корзину)."""
    v = V[lo:hi]
    n = len(v)
    if n == 0:
        return float("nan")
    cum = np.cumsum(v)
    total = float(cum[-1])
    if not total > 0:
        return float("nan")
    cb = np.cumsum(np.where(B[lo:hi], v, 0.0))
    pv = np.r_[0.0, cum]
    pb = np.r_[0.0, cb]
    ks = np.arange(1, 50)
    idx = np.searchsorted(cum, ks * total / 50.0 * (1.0 - VPIN_TOL), "left")
    e = np.r_[0, idx + 1, n]
    tot = pv[e[1:]] - pv[e[:-1]]
    bu = pb[e[1:]] - pb[e[:-1]]
    ok = tot > 0
    if int(ok.sum()) < 10:
        return float("nan")
    return float(np.mean(np.abs(2.0 * bu[ok] - tot[ok]) / tot[ok]))


def q99_edge(hist: np.ndarray):
    """Нижняя граница наименьшего интервала, для которого доля записей с log10 v ≥ границы ≤ 1 %; inf — таких нет; None — нет записей."""
    total = int(hist.sum())
    if total == 0:
        return None
    ge = np.cumsum(hist[::-1])[::-1]
    ok = ge <= 0.01 * total
    if not ok.any():
        return float("inf")
    return EDGES[int(np.argmax(ok))]


def flow_features(T, V, B, sig: int, s: int, q99, has24: bool) -> dict:
    nan = float("nan")
    f = {k: nan for k in FLOW}
    i_hi = int(np.searchsorted(T, sig, "left"))

    def ilo(ms: int) -> int:
        return int(np.searchsorted(T, sig - ms, "left"))

    for w in WIN_S:
        Vt, Vb, N, Nb = win_stats(T, V, B, ilo(w * 1000), i_hi)
        if N > 0:
            f[f"tfv{w}"] = s * (2.0 * Vb - Vt) / Vt if Vt > 0 else nan
            f[f"tfn{w}"] = s * (2.0 * Nb - N) / N
    i60 = ilo(60_000)
    V1, _, N1, _ = win_stats(T, V, B, i60, i_hi)
    Vp, _, Np, _ = win_stats(T, V, B, ilo(31 * MIN_MS), i60)
    if Vp > 0:
        f["acc_v"] = (V1 / 60.0) / (Vp / 1800.0)
    if Np > 0:
        f["acc_n"] = (N1 / 60.0) / (Np / 1800.0)
    i5 = ilo(300_000)
    V5, Vb5, N5, _ = win_stats(T, V, B, i5, i_hi)
    if q99 is not None:
        for w, key in ((300, "big300"), (1800, "big1800")):
            lo = ilo(w * 1000)
            seg = V[lo:i_hi]
            vt = float(seg.sum()) if len(seg) else 0.0
            if vt > 0:
                f[key] = float(seg[seg >= q99].sum()) / vt
    if has24:
        i24 = ilo(DAY_MS)
        V24 = float(V[i24:i_hi].sum()) if i_hi > i24 else 0.0
        if V24 > 0:
            f["tfp300"] = s * (2.0 * Vb5 - V5) / (V24 / 288.0)
        f["vpin"] = vpin_value(V, B, i24, i_hi)
    if N5 > 0 and V5 > 0:
        f["lmsz300"] = math.log10(V5 / N5)
        Vq, _, Nq, _ = win_stats(T, V, B, ilo(35 * MIN_MS), ilo(5 * MIN_MS))
        if Nq > 0 and Vq > 0:
            f["msz_r"] = (V5 / N5) / (Vq / Nq)
    return f


# ───────────────────────────── производные ─────────────────────────────

def load_metrics(sym: str, d0: int, d1: int):
    """Строки `metrics` монеты на сетке 5 мин: словарь поле → массив с NaN; сетка G0 = d0·DAY_MS; первая строка монеты (мс) или None."""
    n = (d1 - d0 + 1) * 288
    cols = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio"]
    arr = {c: np.full(n, np.nan) for c in cols}
    first = None
    for d in range(d0, d1 + 1):
        path = f"{METRICS}/{sym}/{sym}-metrics-{date_str(d)}.zip"
        if not os.path.exists(path):
            continue
        df = read_zip_csv(path, f"{sym}-metrics-{date_str(d)}.csv", ["create_time"] + cols)
        ct = pd.to_datetime(df["create_time"]).astype("int64").to_numpy() // 10**6
        if len(ct) and (first is None or ct.min() < first):
            first = int(ct.min())
        k = (ct - d0 * DAY_MS) // 300_000
        ok = (k >= 0) & (k < n) & ((ct - d0 * DAY_MS) % 300_000 == 0)
        for c in cols:
            arr[c][k[ok]] = df[c].to_numpy(float)[ok]
    return arr, first


def load_funding(sym: str):
    """(время мс, ставка) расчётов фандинга: `funding.csv` (с 01.09) + месячные архивы; дубликаты по времени — из `funding.csv`."""
    rows = {}
    mdir = f"{FUND_M}/{sym}"
    if os.path.isdir(mdir):
        for fn in sorted(os.listdir(mdir)):
            if fn.endswith(".zip"):
                df = read_zip_csv(f"{mdir}/{fn}", fn[:-4] + ".csv", ["calc_time", "last_funding_rate"])
                for t, r in zip(df["calc_time"].to_numpy(np.int64), df["last_funding_rate"].to_numpy(float)):
                    rows[int(t)] = float(r)
    p = f"{FUND_CSV}/{sym}/funding.csv"
    if os.path.exists(p):
        df = pd.read_csv(p, usecols=["funding_time", "funding_rate"])
        for t, r in zip(df["funding_time"].to_numpy(np.int64), df["funding_rate"].to_numpy(float)):
            rows[int(t)] = float(r)
    ts = np.array(sorted(rows), dtype=np.int64)
    return ts, np.array([rows[int(t)] for t in ts])


def load_premium(sym: str, d0: int, d1: int):
    """Закрытие минутной премии на сетке минут от d0·DAY_MS (NaN — нет строки)."""
    n = (d1 - d0 + 1) * 1440
    out = np.full(n, np.nan)
    for d in range(d0, d1 + 1):
        path = f"{PREMIUM}/{sym}/{sym}-1m-{date_str(d)}.zip"
        if not os.path.exists(path):
            continue
        df = read_zip_csv(path, f"{sym}-1m-{date_str(d)}.csv", ["open_time", "close"])
        k = (df["open_time"].to_numpy(np.int64) - d0 * DAY_MS) // MIN_MS
        ok = (k >= 0) & (k < n)
        out[k[ok]] = df["close"].to_numpy(float)[ok]
    return out


def deriv_features(sig: int, turn24h: float, M, first_row, d0: int, ft, fr, prem) -> dict:
    nan = float("nan")
    f = {k: nan for k in DERIV}
    Ts = (sig - 10 * MIN_MS) // 300_000 * 300_000  # T*
    g = (Ts - d0 * DAY_MS) // 300_000
    if first_row is not None and first_row <= Ts - DAY_MS:
        def at(c, back):  # строка на точной отметке T* − back·5 мин
            i = g - back
            return M[c][i] if 0 <= i < len(M[c]) else nan
        oi = at("sum_open_interest", 0)
        for h, key in ((12, "oi_c1"), (48, "oi_c4"), (288, "oi_c24")):
            o2 = at("sum_open_interest", h)
            if oi == oi and o2 == o2 and o2 > 0:
                f[key] = (oi / o2 - 1.0) * 1e4
        oiv = at("sum_open_interest_value", 0)
        if oiv == oiv and oiv > 0 and turn24h > 0:
            f["loi_turn"] = math.log10(oiv / turn24h)
        for c, k1, k2 in (("count_toptrader_long_short_ratio", "ls_tc", "dls_tc"), ("sum_toptrader_long_short_ratio", "ls_ts", "dls_ts"),
                          ("count_long_short_ratio", "ls_a", "dls_a")):
            x, x1 = at(c, 0), at(c, 12)
            if x == x and x > 0:
                f[k1] = math.log(x)
                if x1 == x1 and x1 > 0:
                    f[k2] = math.log(x) - math.log(x1)
    i = int(np.searchsorted(ft, sig, "right")) - 1
    if i >= 0:
        f["fund_last"] = fr[i] * 1e4
        j = int(np.searchsorted(ft, sig - DAY_MS, "right")) - 1
        if j >= 0:
            f["fund_chg24"] = (fr[i] - fr[j]) * 1e4
        if i >= 1:
            f["fund_int"] = float(round((int(ft[i]) - int(ft[i - 1])) / HOUR_MS))
    k = ((sig // MIN_MS) - 1) - (d0 * DAY_MS // MIN_MS)
    if 0 <= k < len(prem) and prem[k] == prem[k]:
        f["basis"] = prem[k] * 1e4
    return f


# ───────────────────────────── монета ─────────────────────────────

def process_coin(args):
    sym, eps, base, mode = args
    t_start = time.time()
    abs_sig = base + eps["sig_ms"].to_numpy(np.int64)
    side = np.where(eps["side"].to_numpy() == "long", 1, -1)
    turn24 = eps["turn24h"].to_numpy(float)
    ep_id = eps["ep_id"].to_numpy()
    days = abs_sig // DAY_MS
    dmin, dmax = int(days.min()), int(days.max())
    out = {k: np.full(len(eps), np.nan) for k in FLOW + DERIV}
    stat = {"sym": sym, "n": len(eps), "missing_trade_days": 0, "coin_first_ms": None}
    # производные (дёшево): строки metrics на сетке с d0 = dmin − 2 (24 ч назад от T* ранних эпизодов суток)
    d0 = dmin - 2
    if mode in ("all", "deriv"):
        M, first_row = load_metrics(sym, d0, dmax)
        ft, fr = load_funding(sym)
        prem = load_premium(sym, d0, dmax)
        for e in range(len(eps)):
            r = deriv_features(int(abs_sig[e]), turn24[e], M, first_row, d0, ft, fr, prem)
            for k in DERIV:
                out[k][e] = r[k]
    if mode in ("all", "flow"):
        # поток: идём по суткам от dmin − 7 (гистограммы q99) до dmax; массивы — текущие и предыдущие сутки
        h0 = (dmin - 8) * 24
        H = np.zeros(((dmax - (dmin - 8) + 1) * 24, NB), np.int32)
        prev = None
        coin_first = None
        by_day = {}
        for e, d in enumerate(days):
            by_day.setdefault(int(d), []).append(e)
        first_seen = False
        for d in range(dmin - 8, dmax + 1):
            cur = read_trades_day(sym, d)
            if cur is not None:
                if not first_seen:
                    first_seen = True
                    coin_first = int(cur[0][0]) if len(cur[0]) else None
                t, v, b = cur
                hr = np.clip(t // HOUR_MS - d * 24, 0, 23)
                ix = np.clip(np.searchsorted(EDGES_ARR, v, "right") - 1, 0, NB - 1)
                H[(d * 24 - h0): (d * 24 - h0) + 24] += np.bincount(hr * NB + ix, minlength=24 * NB).reshape(24, NB).astype(np.int32)
            elif first_seen and d >= dmin - 1:
                stat["missing_trade_days"] += 1
            if d in by_day:
                parts = [p for p in (prev, cur) if p is not None]
                if parts:
                    T = np.concatenate([p[0] for p in parts])
                    V = np.concatenate([p[1] for p in parts])
                    B = np.concatenate([p[2] for p in parts])
                    for e in by_day[d]:
                        sig = int(abs_sig[e])
                        h_end = sig // HOUR_MS
                        sl = H[max(h_end - 168 - h0, 0): h_end - h0]
                        hh = sl.sum(axis=0)
                        n_hours = int((sl.sum(axis=1) > 0).sum())
                        q99 = q99_edge(hh) if n_hours >= 24 else None
                        has24 = coin_first is not None and coin_first <= sig - DAY_MS
                        r = flow_features(T, V, B, sig, int(side[e]), q99, has24)
                        for k in FLOW:
                            out[k][e] = r[k]
            prev = cur
        stat["coin_first_ms"] = coin_first
    res = pd.DataFrame(out)
    res.insert(0, "ep_id", ep_id)
    stat["seconds"] = time.time() - t_start
    return sym, res, stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes")
    ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--coins", default="")
    ap.add_argument("--base-date", default="2026-09-01")
    ap.add_argument("--mode", default="all", choices=["all", "flow", "deriv"])
    ap.add_argument("--max-eps", type=int, default=0, help="отладка: первые N эпизодов каждой монеты")
    a = ap.parse_args()
    t0 = time.time()
    e = pd.read_parquet(a.episodes, columns=["ep_id", "sym", "side", "sig_ms", "day", "turn24h"])
    if a.coins:
        e = e[e.sym.isin(a.coins.split(","))]
    base = base_ms(a.base_date)
    jobs = []
    for sym, g in e.groupby("sym"):
        g = g.sort_values("sig_ms")
        if a.max_eps:
            g = g.head(a.max_eps)
        jobs.append((sym, g.reset_index(drop=True), base, a.mode))
    jobs.sort(key=lambda j: -len(j[1]))
    print(f"эпизодов {len(e)}; монет {len(jobs)}", flush=True)
    parts, stats = [], []
    with Pool(a.procs) as pool:
        for i, (sym, res, st) in enumerate(pool.imap_unordered(process_coin, jobs, chunksize=1)):
            parts.append(res)
            stats.append(st)
            if (i + 1) % 20 == 0:
                print(f"{i + 1}/{len(jobs)} монет, {time.time() - t0:.0f} с", flush=True)
    res = pd.concat(parts, ignore_index=True)
    res.to_parquet(a.out)
    cov = {k: float(res[k].notna().mean()) for k in FLOW + DERIV}
    info = {"episodes": int(len(res)), "seconds": time.time() - t0, "coverage": cov,
            "missing_trade_days": int(sum(s["missing_trade_days"] for s in stats))}
    json.dump(info, open(a.out + ".info.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "coverage"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
