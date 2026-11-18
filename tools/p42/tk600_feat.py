"""П-42 §13.25 (TK-600): токсичный поток `TOX13` по aggTrades + сборка набора X = f12 + FLOW16 + DERIV14 + TOX13 для строк таблиц TK-598.

  python3 tk600_feat.py tox     <eps.parquet> <out.parquet> --base-date 2026-09-01 [--procs 4] [--coins A,B]
      `eps.parquet` — `eps-<прогон>-<вариант>.parquet` TK-598 (столбцы `ep_id, sym, side, sig_ms, day`; метки не открываются); `sig_ms` — мс от `--base-date`.
      Считает 13 признаков §13.25 п. 3 строго по записям `transact_time < σ`; источник — `TK594_TRADES` (как у tk594_feat).
  python3 tk600_feat.py reuse   <eps.parquet> --base-date <T0 eps> --tk594-base-date <T0 TK-594> --tk594-eps <ep_u0|aug_u0.parquet> --tk594-flow <…> --tk594-deriv <…> --have <out> --need <out>
      §13.25 п. 2: строки, чей ключ `(sym, side, base_ms(T0) + sig_ms)` (абсолютные мс UTC) есть у TK-594, берут сохранённые FLOW16 / DERIV14 (`--have`), остальные — в `--need`
      (для `tk594_feat.py --mode all`); `turn24h` пересекающихся строк сверяется (расхождение — стоп).
  python3 tk600_feat.py assemble --f12 <feat-….parquet> --have <…> [--computed <tk594-вывод для --need>] --tox <…> --out <feat-X-….parquet> --eps <eps.parquet>
      Ключ `(sym, level, sig_ms)` + `tick_ok` + f12 + FLOW16 + DERIV14 + TOX13; строки 1:1 с `--f12` (ворота G-K).
  python3 tk600_feat.py gate-reuse --eps <eps.parquet> --have <…> --tk594-eps … --n 150 --base-date …   (ворота G-R)

Определения — docs/research/P-42-level-breakout.md §13.25 п. 3. Код TK-594 (`tk594_feat.py`) не меняется: берутся его `vpin_value`, `q99_edge`, гистограмма (`EDGES_ARR`, `NB`).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import zipfile
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk594_feat as F4  # noqa: E402

MIN_MS, HOUR_MS, DAY_MS = F4.MIN_MS, F4.HOUR_MS, F4.DAY_MS
TOX = ["vpin1h", "vpin6h", "kyle30", "kyle240", "amih30", "amih240", "bigimb300", "bigimb1800", "sac300", "sac1800", "mk10_300", "mk60_300", "mk60_1800"]
F12 = ["pierce_ep", "spread_tau", "zv6h", "tpm60", "mv5", "cons60", "c5_btc_7d", "c5_eth_7d", "lturn24", "age_h", "natr_bp", "since_touch_prior_h"]
X_FEATS = F12 + F4.FLOW + F4.DERIV + TOX          # 55 признаков, порядок фиксирован (G-P)
KEY = ["sym", "level", "sig_ms"]
SAC_MIN_N = 30
MK_MIN_N = 10
NAN = float("nan")


# ───────────────────────────── чтение ─────────────────────────────

def read_trades_day_p(sym: str, abs_day: int):
    """Как `tk594_feat.read_trades_day`, но с ценой: (t мс, p, v = p·q, покупка тейкера); None — файла нет. Порядок файла; при убывании времени — устойчивая сортировка."""
    name = f"{sym}-aggTrades-{F4.date_str(abs_day)}"
    for root in F4.TRADES:
        path = f"{root}/{sym}/{name}.zip"
        if not os.path.exists(path):
            continue
        with zipfile.ZipFile(path) as zf, zf.open(name + ".csv") as fh:
            df = pd.read_csv(fh, usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
                             dtype={"price": np.float64, "quantity": np.float64, "transact_time": np.int64})
        t = df["transact_time"].to_numpy()
        p = df["price"].to_numpy()
        v = p * df["quantity"].to_numpy()
        col = df["is_buyer_maker"]
        maker = col.to_numpy() if col.dtype == bool else col.astype(str).str.lower().eq("true").to_numpy()
        buy = ~maker
        if len(t) > 1 and np.any(np.diff(t) < 0):
            o = np.argsort(t, kind="stable")
            t, p, v, buy = t[o], p[o], v[o], buy[o]
        return t, p, v, buy
    return None


# ───────────────────────────── признаки ─────────────────────────────

def minute_features(T, P, V, B, sig: int, W: int, avail_from: int):
    """(kyle_W, amih_W) по `W` полным минутам перед `σ` (§13.25 п. 3.2); T, P, V, B — записи с t < σ."""
    m = sig // MIN_MS
    if (m - W) * MIN_MS < avail_from:
        return NAN, NAN
    ebounds = (np.arange(m - W, m + 1, dtype=np.int64)) * MIN_MS            # границы W баров
    edges = np.searchsorted(T, ebounds, "left")                              # edges[j] — первая запись с t ≥ ebounds[j]
    if edges[0] == 0:
        return NAN, NAN                                                      # нет цены до окна
    Pend = P[edges - 1]                                                      # цена конца бара j−1 (j = 0 — цена до окна)
    if not np.all(Pend > 0):
        return NAN, NAN
    r = 1e4 * np.log(Pend[1:] / Pend[:-1])
    lo, hi = int(edges[0]), int(edges[-1])
    seg_v, seg_b = V[lo:hi], B[lo:hi]
    bar = np.searchsorted(ebounds, T[lo:hi], "right") - 1                    # бар записи, 0…W−1
    Vk = np.bincount(bar, weights=seg_v, minlength=W)
    Qk = np.bincount(bar, weights=np.where(seg_b, seg_v, -seg_v), minlength=W)
    active = Vk > 0
    if int(active.sum()) < -(-W // 3):
        return NAN, NAN
    qd = Qk - Qk.mean()
    den = float((qd * qd).sum())
    kyle = NAN
    if den > 0:
        lam = float((qd * (r - r.mean())).sum()) / den
        kyle = lam * float(Vk.mean())
    amih = float(np.mean(np.abs(r[active]) / (Vk[active] / 1e6)))
    return kyle, amih


def big_imbalance(T, V, B, sig: int, W_ms: int, q99, s: int):
    if q99 is None:
        return NAN
    lo = int(np.searchsorted(T, sig - W_ms, "left"))
    v, b = V[lo:], B[lo:]
    big = v >= q99
    vt = float(v[big].sum())
    if not vt > 0:
        return NAN
    return s * (2.0 * float(v[big & b].sum()) - vt) / vt


def sign_autocorr(T, B, sig: int, W_ms: int):
    lo = int(np.searchsorted(T, sig - W_ms, "left"))
    d = np.where(B[lo:], 1.0, -1.0)
    n = len(d)
    if n < SAC_MIN_N:
        return NAN
    dc = d - d.mean()
    den = float((dc * dc).sum())
    if not den > 0:
        return NAN
    return float((dc[:-1] * dc[1:]).sum()) / den


def markout(T, P, V, B, sig: int, h_ms: int, W_ms: int):
    lo = int(np.searchsorted(T, sig - W_ms, "left"))
    hi = int(np.searchsorted(T, sig - h_ms, "left"))                         # записи с t + h < σ
    if hi - lo < MK_MIN_N:
        return NAN
    t = T[lo:hi]
    fut = np.searchsorted(T, t + h_ms, "right") - 1                          # последняя запись с t ≤ t_i + h (всегда < σ)
    pf, p0 = P[fut], P[lo:hi]
    if not (np.all(pf > 0) and np.all(p0 > 0)):
        return NAN
    mk = np.where(B[lo:hi], 1.0, -1.0) * 1e4 * np.log(pf / p0)
    v = V[lo:hi]
    return float((v * mk).sum() / v.sum())


def tox_features(T, P, V, B, sig: int, s: int, q99, coin_first, avail_from: int) -> dict:
    """TOX13 строки. T, P, V, B — записи монеты в порядке времени (могут содержать t ≥ σ — отрезаются); `avail_from` — начало загруженных данных (мс)."""
    i_hi = int(np.searchsorted(T, sig, "left"))
    T, P, V, B = T[:i_hi], P[:i_hi], V[:i_hi], B[:i_hi]
    f = {k: NAN for k in TOX}
    if i_hi == 0:
        return f
    for w_h, key in ((1, "vpin1h"), (6, "vpin6h")):
        w = w_h * HOUR_MS
        if coin_first is not None and coin_first <= sig - w and sig - w >= avail_from:
            f[key] = F4.vpin_value(V, B, int(np.searchsorted(T, sig - w, "left")), i_hi)
    for W, kk, ka in ((30, "kyle30", "amih30"), (240, "kyle240", "amih240")):
        f[kk], f[ka] = minute_features(T, P, V, B, sig, W, avail_from)
    for w_s, key in ((300, "bigimb300"), (1800, "bigimb1800")):
        if sig - w_s * 1000 >= avail_from:
            f[key] = big_imbalance(T, V, B, sig, w_s * 1000, q99, s)
    for w_s, key in ((300, "sac300"), (1800, "sac1800")):
        if sig - w_s * 1000 >= avail_from:
            f[key] = sign_autocorr(T, B, sig, w_s * 1000)
    for h_s, w_s, key in ((10, 300, "mk10_300"), (60, 300, "mk60_300"), (60, 1800, "mk60_1800")):
        if sig - w_s * 1000 >= avail_from:
            f[key] = markout(T, P, V, B, sig, h_s * 1000, w_s * 1000)
    return f


# ───────────────────────────── монета ─────────────────────────────

def process_coin(args):
    """Как `tk594_feat.process_coin` (режим flow): сутки от dmin−8 (гистограмма q99) до dmax; массивы — предыдущие и текущие сутки."""
    sym, eps, base = args
    t_start = time.time()
    abs_sig = base + eps["sig_ms"].to_numpy(np.int64)
    side = np.where(eps["side"].to_numpy() == "long", 1, -1)
    ep_id = eps["ep_id"].to_numpy()
    days = abs_sig // DAY_MS
    dmin, dmax = int(days.min()), int(days.max())
    out = {k: np.full(len(eps), np.nan) for k in TOX}
    stat = {"sym": sym, "n": len(eps), "missing_trade_days": 0, "rows_on_missing_day": 0}
    h0 = (dmin - 8) * 24
    H = np.zeros(((dmax - (dmin - 8) + 1) * 24, F4.NB), np.int32)
    by_day = {}
    for e, d in enumerate(days):
        by_day.setdefault(int(d), []).append(e)
    prev, coin_first, first_seen = None, None, False
    for d in range(dmin - 8, dmax + 1):
        cur = read_trades_day_p(sym, d)
        if cur is not None:
            if not first_seen:
                first_seen = True
                coin_first = int(cur[0][0]) if len(cur[0]) else None
            t, _p, v, _b = cur
            hr = np.clip(t // HOUR_MS - d * 24, 0, 23)
            ix = np.clip(np.searchsorted(F4.EDGES_ARR, v, "right") - 1, 0, F4.NB - 1)
            H[(d * 24 - h0): (d * 24 - h0) + 24] += np.bincount(hr * F4.NB + ix, minlength=24 * F4.NB).reshape(24, F4.NB).astype(np.int32)
        elif first_seen and d >= dmin - 1:
            stat["missing_trade_days"] += 1
        if d in by_day:
            if cur is None:                                                   # сутки строки без файла — признаки пусты (дыра данных)
                stat["rows_on_missing_day"] += len(by_day[d])
            else:
                parts = [p for p in (prev, cur) if p is not None]
                avail_from = d * DAY_MS if prev is None else (d - 1) * DAY_MS
                T = np.concatenate([p[0] for p in parts])
                P = np.concatenate([p[1] for p in parts])
                V = np.concatenate([p[2] for p in parts])
                B = np.concatenate([p[3] for p in parts])
                for e in by_day[d]:
                    sig = int(abs_sig[e])
                    h_end = sig // HOUR_MS
                    sl = H[max(h_end - 168 - h0, 0): h_end - h0]
                    n_hours = int((sl.sum(axis=1) > 0).sum())
                    q99 = F4.q99_edge(sl.sum(axis=0)) if n_hours >= 24 else None
                    r = tox_features(T, P, V, B, sig, int(side[e]), q99, coin_first, avail_from)
                    for k in TOX:
                        out[k][e] = r[k]
        prev = cur
    res = pd.DataFrame(out)
    res.insert(0, "ep_id", ep_id)
    stat["seconds"] = time.time() - t_start
    return sym, res, stat


def cmd_tox(a):
    t0 = time.time()
    e = pd.read_parquet(a.eps, columns=["ep_id", "sym", "side", "sig_ms", "day"])
    if a.coins:
        e = e[e.sym.isin(a.coins.split(","))]
    base = F4.base_ms(a.base_date)
    jobs = [(sym, g.sort_values("sig_ms").reset_index(drop=True), base) for sym, g in e.groupby("sym")]
    jobs.sort(key=lambda j: -len(j[1]))
    print(f"строк {len(e)}; монет {len(jobs)}", flush=True)
    parts, stats = [], []
    with Pool(a.procs) as pool:
        for i, (sym, res, st) in enumerate(pool.imap_unordered(process_coin, jobs, chunksize=1)):
            parts.append(res)
            stats.append(st)
            if (i + 1) % 20 == 0:
                print(f"{i + 1}/{len(jobs)} монет, {time.time() - t0:.0f} с", flush=True)
    res = pd.concat(parts, ignore_index=True)
    res.to_parquet(a.out)
    info = {"rows": int(len(res)), "seconds": time.time() - t0, "coverage": {k: float(res[k].notna().mean()) for k in TOX},
            "missing_trade_days": int(sum(s["missing_trade_days"] for s in stats)), "rows_on_missing_day": int(sum(s["rows_on_missing_day"] for s in stats))}
    with open(a.out + ".info.json", "w", encoding="utf-8") as _f:
        json.dump(info, _f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "coverage"}, ensure_ascii=False), flush=True)


# ───────────────────────────── переиспользование TK-594 ─────────────────────────────

def cmd_reuse(a):
    """Ключ сопоставления — `(sym, side, base_ms(T0 окна) + sig_ms)` в абсолютных мс UTC (§13.25 п. 2); T0 обеих таблиц печатается; turn24h пересекающихся строк сверяется (G-R)."""
    e = pd.read_parquet(a.eps, columns=["ep_id", "sym", "side", "sig_ms", "day", "turn24h"])
    t = pd.read_parquet(a.tk594_eps, columns=["ep_id", "sym", "side", "sig_ms", "turn24h"])
    e["abs_ms"] = e["sig_ms"].astype(np.int64) + F4.base_ms(a.base_date)
    t["abs_ms"] = t["sig_ms"].astype(np.int64) + F4.base_ms(a.tk594_base_date)
    if t.duplicated(["sym", "side", "abs_ms"]).any() or e.duplicated(["sym", "side", "abs_ms"]).any():
        raise SystemExit("дубль (sym, side, abs_ms)")
    fl = pd.read_parquet(a.tk594_flow)[["ep_id"] + F4.FLOW]
    dv = pd.read_parquet(a.tk594_deriv)[["ep_id"] + F4.DERIV]
    t = t.merge(fl, on="ep_id", how="left", validate="one_to_one").merge(dv, on="ep_id", how="left", validate="one_to_one")
    t = t.drop(columns=["ep_id", "sig_ms"]).rename(columns={"turn24h": "turn24h_594"})
    m = e[["ep_id", "sym", "side", "abs_ms", "turn24h"]].merge(t, on=["sym", "side", "abs_ms"], how="left", indicator=True, validate="one_to_one")
    both = m[m["_merge"] == "both"]
    bad_turn = int((~(np.isclose(both["turn24h"].to_numpy(float), both["turn24h_594"].to_numpy(float), rtol=1e-12, atol=0.0)
                      | (both["turn24h"].isna() & both["turn24h_594"].isna()))).sum())
    have = both.drop(columns=["_merge", "sym", "side", "abs_ms", "turn24h", "turn24h_594"]).reset_index(drop=True)
    need = e[~e["ep_id"].isin(have["ep_id"])].drop(columns=["abs_ms"]).reset_index(drop=True)
    have.to_parquet(a.have)
    need.to_parquet(a.need)
    info = {"T0_eps": a.base_date, "T0_tk594": a.tk594_base_date, "rows": int(len(e)), "reused": int(len(have)), "to_compute": int(len(need)), "turn24h_mismatch": bad_turn}
    print(json.dumps(info, ensure_ascii=False))
    if bad_turn:
        raise SystemExit("G-R: turn24h не совпал у пересекающихся строк")


def cmd_assemble(a):
    f12 = pd.read_parquet(a.f12)
    f12["level"] = f12["level"].astype(np.int64)
    f12["sig_ms"] = f12["sig_ms"].astype(np.int64)
    e = pd.read_parquet(a.eps, columns=["ep_id"] + KEY)
    e["level"] = e["level"].astype(np.int64)
    e["sig_ms"] = e["sig_ms"].astype(np.int64)
    parts = [pd.read_parquet(a.have)]
    if a.computed:
        parts.append(pd.read_parquet(a.computed)[["ep_id"] + F4.FLOW + F4.DERIV])
    td = pd.concat(parts, ignore_index=True)
    if td["ep_id"].duplicated().any():
        raise SystemExit("ep_id продублирован между reuse и computed")
    tox = pd.read_parquet(a.tox)[["ep_id"] + TOX]
    ex = e.merge(td, on="ep_id", how="left", validate="one_to_one").merge(tox, on="ep_id", how="left", validate="one_to_one")
    if len(ex) != len(f12) or ex[KEY].duplicated().any():
        raise SystemExit("G-K: строки eps не совпадают 1:1 с f12")
    out = f12.merge(ex.drop(columns=["ep_id"]), on=KEY, how="left", validate="one_to_one")
    if len(out) != len(f12):
        raise SystemExit("G-K: число строк изменилось после слияния")
    missing_rows = int(out[F4.FLOW + F4.DERIV + TOX].isna().all(axis=1).sum())
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out.to_parquet(a.out)
    info = {"rows": int(len(out)), "rows_all_extra_nan": missing_rows, "coverage": {c: float(out[c].notna().mean()) for c in F4.FLOW + F4.DERIV + TOX}}
    with open(a.out + ".info.json", "w", encoding="utf-8") as _f:
        json.dump(info, _f, ensure_ascii=False, indent=1)
    print(json.dumps({"rows": info["rows"], "rows_all_extra_nan": missing_rows}, ensure_ascii=False))


def cmd_gate_reuse(a):
    """G-R: n случайных строк из `--have` пересчитываются `tk594_feat.process_coin`; допуск 1e-12 отн.; печатается число несовпадений."""
    e = pd.read_parquet(a.eps, columns=["ep_id", "sym", "side", "sig_ms", "day", "turn24h"])
    have = pd.read_parquet(a.have)
    rows = e[e["ep_id"].isin(have["ep_id"])].sample(n=min(a.n, len(have)), random_state=600)
    base = F4.base_ms(a.base_date)
    bad, cmp_n = 0, 0
    for sym, g in rows.groupby("sym"):
        g = g.sort_values("sig_ms").reset_index(drop=True)
        _s, res, _st = F4.process_coin((sym, g, base, "all"))
        ref = have.merge(res, on="ep_id", suffixes=("_h", "_c"))
        for c in F4.FLOW + F4.DERIV:
            x, y = ref[c + "_h"].to_numpy(float), ref[c + "_c"].to_numpy(float)
            both_nan = np.isnan(x) & np.isnan(y)
            diff = ~both_nan & ~(np.abs(x - y) <= 1e-12 * np.maximum(1.0, np.maximum(np.abs(x), np.abs(y))))
            bad += int(diff.sum())
            cmp_n += int(len(x))
    print(json.dumps({"rows": int(len(rows)), "values_compared": cmp_n, "mismatches": bad}, ensure_ascii=False))
    if bad:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("tox")
    p.add_argument("eps"); p.add_argument("out"); p.add_argument("--base-date", required=True); p.add_argument("--procs", type=int, default=4); p.add_argument("--coins", default="")
    p = sp.add_parser("reuse")
    p.add_argument("eps"); p.add_argument("--base-date", required=True); p.add_argument("--tk594-base-date", required=True)
    p.add_argument("--tk594-eps", required=True); p.add_argument("--tk594-flow", required=True); p.add_argument("--tk594-deriv", required=True)
    p.add_argument("--have", required=True); p.add_argument("--need", required=True)
    p = sp.add_parser("assemble")
    p.add_argument("--f12", required=True); p.add_argument("--eps", required=True); p.add_argument("--have", required=True); p.add_argument("--computed", default="")
    p.add_argument("--tox", required=True); p.add_argument("--out", required=True)
    p = sp.add_parser("gate-reuse")
    p.add_argument("--eps", required=True); p.add_argument("--have", required=True); p.add_argument("--base-date", required=True); p.add_argument("--n", type=int, default=150)
    a = ap.parse_args()
    {"tox": cmd_tox, "reuse": cmd_reuse, "assemble": cmd_assemble, "gate-reuse": cmd_gate_reuse}[a.cmd](a)


if __name__ == "__main__":
    main()
