#!/usr/bin/env python3
"""П-42 §13.21 (TK-595): признаки книги (`bookDepth` Binance USD-M) по строкам таблицы эпизодов `ep_u0` (стадия меток).

  python3 bookdepth_feat.py feat <ep_u0.parquet> <каталог zip> <выход.parquet> [--half A] [--procs 4]
  python3 bookdepth_feat.py selftest                         # ворота G-B2 (причинное правило) и G-B3 (усечение), синтетика
  python3 bookdepth_feat.py gate-trunc <ep_u0> <zip> <N>     # G-B3 на N случайных строках: усечь снимки позже sig_ms − 30 с → признаки те же

Определения (фиксируются до чтения z; см. docs/findings/p42-retitration-labels-columns.txt):
  снимок = строки одного `timestamp` (UTC, ≈ каждые 30 с), 12 полос ±0,2 / 1 / 2 / 3 / 4 / 5 %, `notional` — накопленный объём в USDT
  внутри полосы (минус — бид, плюс — аск).
  Причинное правило (G-B2): берётся последний снимок с ts + 30 с ≤ sig (sig = 2026-09-01 00:00:00 UTC + sig_ms); если его возраст
  (sig − ts) > 600 с — признак NaN (дыра в архиве).
  N_lv — notional на стороне уровня (лонг: аски выше цены), N_push — на противоположной (лонг: биды); шорт зеркально.
  bd_imb02 / bd_imb1 / bd_imb2 = (N_push − N_lv)/(N_push + N_lv) на ±0,2 / 1 / 2 %.
  bd_thin5 / bd_thin30 = ln(N_lv(1 %) снимка / N_lv(1 %) снимка на 10 / 60 снимков раньше) — окно задано числом снимков по индексу.
  bd_depth_turn = N_lv(1 %) / turn1h (turn1h — оборот последнего часа из `ep_u0`, USDT).
"""
import datetime as dt
import io
import os
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp()  # sig_ms — смещение от этой точки
BANDS = [-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0]
BI = {b: i for i, b in enumerate(BANDS)}
LAG_S = 30.0
MAX_AGE_S = 600.0
FEATS = ["bd_imb02", "bd_imb1", "bd_imb2", "bd_thin5", "bd_thin30", "bd_depth_turn"]


def load_day(zdir, sym, day):
    p = os.path.join(zdir, sym, "%s-bookDepth-%s.zip" % (sym, day))
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        return None
    with zipfile.ZipFile(p) as z:
        df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    ts = pd.to_datetime(df["timestamp"], utc=True)
    df["ts"] = (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    w = df.pivot_table(index="ts", columns="percentage", values="notional", aggfunc="last")
    w = w.reindex(columns=BANDS)
    return w.index.values.astype(np.float64), w.values.astype(np.float64)


def load_sym(zdir, sym, days):
    parts = [load_day(zdir, sym, d) for d in days]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    t = np.concatenate([p[0] for p in parts])
    m = np.vstack([p[1] for p in parts])
    o = np.argsort(t, kind="stable")
    return t[o], m[o]


def features_at(t, m, sig_s, long_side, turn1h, trunc=False):
    """Признаки на момент sig_s; trunc=True — снимки позже sig_s − 30 с физически отброшены (проверка G-B3)."""
    if trunc:
        keep = t <= sig_s - LAG_S
        t, m = t[keep], m[keep]
    i = int(np.searchsorted(t, sig_s - LAG_S, side="right")) - 1
    nan = {k: np.nan for k in FEATS}
    if i < 0 or sig_s - t[i] > MAX_AGE_S:
        return nan

    def nlv(row, b):
        return row[BI[b]] if long_side else row[BI[-b]]

    def npush(row, b):
        return row[BI[-b]] if long_side else row[BI[b]]

    row = m[i]
    out = dict(nan)
    for name, b in (("bd_imb02", 0.2), ("bd_imb1", 1.0), ("bd_imb2", 2.0)):
        a, p = nlv(row, b), npush(row, b)
        if np.isfinite(a) and np.isfinite(p) and a + p > 0:
            out[name] = (p - a) / (p + a)
    a1 = nlv(row, 1.0)
    for name, lag in (("bd_thin5", 10), ("bd_thin30", 60)):
        if i - lag >= 0:
            b1 = nlv(m[i - lag], 1.0)
            if np.isfinite(a1) and np.isfinite(b1) and a1 > 0 and b1 > 0:
                out[name] = float(np.log(a1 / b1))
    if np.isfinite(a1) and a1 > 0 and turn1h and turn1h > 0:
        out["bd_depth_turn"] = float(a1 / turn1h)
    return out


def _one_sym(args):
    sym, rows, zdir = args  # rows: list of (ep_id, side, sig_ms, turn1h)
    days = set()
    for _, _, sig_ms, _ in rows:
        s = T0 + sig_ms / 1000.0
        days.add(dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%Y-%m-%d"))
        days.add(dt.datetime.fromtimestamp(s - 3600, dt.timezone.utc).strftime("%Y-%m-%d"))
    tm = load_sym(zdir, sym, sorted(days))
    res = []
    for ep_id, side, sig_ms, turn1h in rows:
        if tm is None:
            res.append((ep_id, {k: np.nan for k in FEATS}))
        else:
            res.append((ep_id, features_at(tm[0], tm[1], T0 + sig_ms / 1000.0, side == "long", turn1h)))
    return res


def cmd_feat(ep, zdir, out, half="A", procs=4):
    d = pd.read_parquet(ep, columns=["ep_id", "sym", "side", "sig_ms", "turn1h", "half"])
    d = d[d["half"] == half]
    jobs = [(s, list(zip(g["ep_id"], g["side"], g["sig_ms"], g["turn1h"])), zdir) for s, g in d.groupby("sym")]
    recs = []
    with ProcessPoolExecutor(procs) as ex:
        for k, r in enumerate(ex.map(_one_sym, jobs, chunksize=1), 1):
            for ep_id, f in r:
                recs.append(dict(ep_id=ep_id, **f))
            if k % 50 == 0:
                print("%d/%d монет" % (k, len(jobs)), flush=True)
    df = pd.DataFrame(recs)
    df.to_parquet(out, index=False)
    print("rows", len(df), {k: round(float(df[k].isna().mean()), 3) for k in FEATS})


def selftest():
    # синтетика: 3 снимка на 30 с, пять минут
    n = 40
    t = 1000.0 + 30.0 * np.arange(n)
    m = np.ones((n, 12)) * 1e6
    m[:, BI[0.2]] = 2e6 + np.arange(n) * 1e4  # ask 0,2
    m[:, BI[-0.2]] = 1e6
    m[:, BI[1.0]] = 5e6 * np.exp(-0.01 * np.arange(n))  # ask 1 % тончает
    m[:, BI[-1.0]] = 4e6
    sig = t[30] + 30.0  # последний допустимый снимок — 30 (ts + 30 ≤ sig)
    f = features_at(t, m, sig, True, 1e7)
    assert abs(f["bd_imb02"] - (1e6 - m[30, BI[0.2]]) / (1e6 + m[30, BI[0.2]])) < 1e-12, f
    assert abs(f["bd_thin5"] - np.log(m[30, BI[1.0]] / m[20, BI[1.0]])) < 1e-12, f
    # G-B2: снимок ровно на sig − 29 с НЕ берётся, берётся предыдущий
    f2 = features_at(t, m, t[30] + 29.0, True, 1e7)
    assert abs(f2["bd_thin5"] - np.log(m[29, BI[1.0]] / m[19, BI[1.0]])) < 1e-12, f2
    # G-B3: усечение (физически отбросить снимки > sig − 30 с) не меняет признаки
    for s in (t[15] + 31.0, t[30] + 30.0, t[38] + 45.0):
        a = features_at(t, m, s, True, 1e7)
        b = features_at(t, m, s, True, 1e7, trunc=True)
        assert all((np.isnan(a[k]) and np.isnan(b[k])) or a[k] == b[k] for k in FEATS), (s, a, b)
    # шорт зеркально
    fs = features_at(t, m, sig, False, 1e7)
    assert abs(fs["bd_imb02"] - (m[30, BI[0.2]] - 1e6) / (m[30, BI[0.2]] + 1e6)) < 1e-12
    # дыра: возраст > 600 с → NaN
    assert np.isnan(features_at(t, m, t[-1] + 700.0, True, 1e7)["bd_imb1"])
    # нет 60 снимков назад → thin30 NaN
    assert np.isnan(f["bd_thin30"])
    print("selftest OK")


def cmd_gate_trunc(ep, zdir, n):
    d = pd.read_parquet(ep, columns=["ep_id", "sym", "side", "sig_ms", "turn1h", "half"])
    d = d[d["half"] == "A"].sample(n=int(n), random_state=595)
    bad = ok = 0
    for r in d.itertuples():
        s = T0 + r.sig_ms / 1000.0
        days = sorted({dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%d") for x in (s, s - 3600)})
        tm = load_sym(zdir, r.sym, days)
        if tm is None:
            continue
        a = features_at(tm[0], tm[1], s, r.side == "long", r.turn1h)
        b = features_at(tm[0], tm[1], s, r.side == "long", r.turn1h, trunc=True)
        same = all((np.isnan(a[k]) and np.isnan(b[k])) or a[k] == b[k] for k in FEATS)
        ok += same
        bad += not same
    print("G-B3 gate-trunc: same=%d differ=%d" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "selftest":
        selftest()
    elif c == "feat":
        a = sys.argv
        kw = dict(zip(a[5::2], a[6::2]))
        cmd_feat(a[2], a[3], a[4], kw.get("--half", "A"), int(kw.get("--procs", 4)))
    elif c == "gate-trunc":
        sys.exit(cmd_gate_trunc(sys.argv[2], sys.argv[3], sys.argv[4]))
