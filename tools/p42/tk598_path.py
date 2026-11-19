"""П-42 §13.23 (TK-598), цель T5: MAE / MFE по ленте aggTrades для сделок `detail.jsonl` — пакетно (одно чтение суток монеты на все её сделки).

Формулы — как `path_report.analyse` (§13.19, `path_report.py:77–82`): длинная рама (шорт × −1), цена входа сделки (`entry_px_e8` = лот 1 при `e2`), окно
`entry_ms < t ≤ exit_ms`, `mfe = max(0, (max P − entry)/|entry|)`, `mae = max(0, (entry − min P)/|entry|)`, `*_natr = * / (natr_e8/1e8)`. Эквивалентность
проверяет ворота G-M (`gate`: 200 случайных сделок против `path_report.analyse`, допуск 1e-9). Отличие от `path_report`: окно ленты — только от входа до выхода
(без +60 мин после выхода), `T0` окна — параметр (в `path_report.py` зашит 01.09.2026).

  python3 tk598_path.py run --detail <side-detail.jsonl> --t0 YYYY-MM-DD --agg <каталог сделок> --out <paths.parquet> [--procs 6]
  python3 tk598_path.py gate --detail <side-detail.jsonl> --t0 2026-09-01 --agg <каталог> [--n 200] [--seed 598]
Выход: parquet `sym, level, sig_ms, mfe_pct, mae_pct, mfe_natr, mae_natr, n_ticks, exit_tick_ok` (ключ — как в trades.csv / detail.jsonl).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import zipfile
from multiprocessing import Pool

import numpy as np

DAY = 86_400_000


def t0_ms(t0: str) -> int:
    return int(dt.datetime.fromisoformat(t0).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def read_detail(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_day(agg, sym, day_iso):
    """Сделки монеты за сутки: (t мс int64, p ×1e8 int64) по возрастанию t (стабильно); пусто, если файла нет. Первая строка — заголовок (как в path_report)."""
    import pandas as pd
    z = f"{agg}/{sym}/{sym}-aggTrades-{day_iso}.zip"
    if not os.path.exists(z):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    with zipfile.ZipFile(z) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            df = pd.read_csv(fh, header=0, names=["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"],
                             usecols=["price", "transact_time"], dtype={"price": np.float64, "transact_time": np.int64}, engine="c")
    p = np.rint(df["price"].to_numpy(np.float64) * 1e8).astype(np.int64)
    t = df["transact_time"].to_numpy(np.int64)
    o = np.argsort(t, kind="stable")
    return t[o], p[o]


def extract(agg, sym, t0, lo_abs, hi_abs, cache):
    """Сделки монеты с transact_time в [lo_abs, hi_abs] (абс. мс): сутки — от (lo − T0)//DAY до (hi − T0)//DAY, как у path_report.extract."""
    ts, ps = [], []
    base = dt.datetime.fromtimestamp(t0 / 1000, dt.timezone.utc).date()
    for d in range((lo_abs - t0) // DAY, (hi_abs - t0) // DAY + 1):
        day = (base + dt.timedelta(d)).isoformat()
        if day not in cache:
            cache[day] = load_day(agg, sym, day)
        t, p = cache[day]
        if len(t) == 0:
            continue
        m = (t >= lo_abs) & (t <= hi_abs)
        if m.any():
            ts.append(t[m])
            ps.append(p[m])
    if not ts:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    t = np.concatenate(ts)
    p = np.concatenate(ps)
    o = np.argsort(t, kind="stable")
    return t[o], p[o]


def one(r, t0, agg, cache):
    """Строка detail → MAE/MFE (см. формулы в шапке)."""
    s = 1 if r["side"] == "long" else -1
    entry_abs, exit_abs = t0 + r["entry_ms"], t0 + r["exit_ms"]
    t, p8 = extract(agg, r["sym"], t0, entry_abs, exit_abs, cache)
    ef = s * r["entry_px_e8"]
    natr = r["natr_e8"] / 1e8
    hold = (t > entry_abs) & (t <= exit_abs)
    th, ph = t[hold], s * p8[hold]
    d = dict(sym=r["sym"], level=int(r["level"]), sig_ms=int(r["sig_ms"]), n_ticks=int(hold.sum()),
             exit_tick_ok=bool(len(th) and th[-1] == exit_abs))
    if len(th) == 0:
        d.update(mfe_pct=0.0, mae_pct=0.0, mfe_natr=np.nan, mae_natr=np.nan)
        return d
    ea = abs(ef)
    mfe = max(0.0, (ph.max() - ef) / ea)
    mae = max(0.0, (ef - ph.min()) / ea)
    d.update(mfe_pct=mfe * 100, mae_pct=mae * 100, mfe_natr=mfe / natr if natr > 0 else np.nan, mae_natr=mae / natr if natr > 0 else np.nan)
    return d


def _job(args):
    sym, rows, t0, agg = args
    rows = sorted(rows, key=lambda r: r["entry_ms"])
    out = []
    cache: dict = {}
    last_day = None
    for r in rows:
        d0 = (t0 + r["entry_ms"] - t0) // DAY
        if last_day is not None and d0 > last_day + 1:
            cache.clear()          # память: сутки позади сделок монеты не нужны
        out.append(one(r, t0, agg, cache))
        last_day = max(last_day or 0, d0)
    return out


def run(detail, t0, agg, procs=6):
    import pandas as pd
    rows = read_detail(detail)
    t0m = t0_ms(t0)
    by = {}
    for r in rows:
        by.setdefault(r["sym"], []).append(r)
    jobs = [(sym, rs, t0m, agg) for sym, rs in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    recs = []
    with Pool(procs) as pool:
        for i, out in enumerate(pool.imap_unordered(_job, jobs, chunksize=1)):
            recs += out
            if (i + 1) % 50 == 0:
                print(f"{i + 1}/{len(jobs)} монет", flush=True)
    df = pd.DataFrame(recs)
    return df


def gate(detail, t0, agg, n=200, seed=598):
    """G-M: пакетная реализация против path_report.analyse (импорт; его T0 и сутки зашиты на 01.09.2026 — только окно F)."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import path_report as pr
    pr.AGG = agg
    rows = read_detail(detail)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(rows), size=min(n, len(rows)), replace=False)
    t0m = t0_ms(t0)
    assert t0m == pr.T0, "ворота G-M — только окно F (T0 = 01.09.2026)"
    bad = 0
    mx = 0.0
    for i in idx:
        r = rows[int(i)]
        a = one(r, t0m, agg, {})
        b = pr.analyse(r)
        for k_a, k_b in (("mfe_pct", "mfe_pct"), ("mae_pct", "mae_pct"), ("mfe_natr", "mfe_natr"), ("mae_natr", "mae_natr")):
            va, vb = a.get(k_a, np.nan), b.get(k_b, np.nan)
            if np.isnan(va) and np.isnan(vb):
                continue
            if np.isnan(va) != np.isnan(vb):
                bad += 1
                continue
            d = abs(va - vb)
            mx = max(mx, d)
            if d > 1e-9:
                bad += 1
    res = {"n": int(len(idx)), "mismatch": int(bad), "max_abs_diff": mx}
    print(json.dumps(res))
    return res


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("run")
    a.add_argument("--detail", required=True)
    a.add_argument("--t0", required=True)
    a.add_argument("--agg", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--procs", type=int, default=6)
    g = sp.add_parser("gate")
    g.add_argument("--detail", required=True)
    g.add_argument("--t0", required=True)
    g.add_argument("--agg", required=True)
    g.add_argument("--n", type=int, default=200)
    g.add_argument("--seed", type=int, default=598)
    ns = ap.parse_args()
    if ns.cmd == "run":
        df = run(ns.detail, ns.t0, ns.agg, ns.procs)
        df.to_parquet(ns.out)
        print(json.dumps({"rows": int(len(df)), "no_ticks": int((df.n_ticks == 0).sum())}))
    else:
        r = gate(ns.detail, ns.t0, ns.agg, ns.n, ns.seed)
        sys.exit(0 if r["mismatch"] == 0 else 1)


if __name__ == "__main__":
    main()
