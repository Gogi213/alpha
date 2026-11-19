"""TK-618 шаг 1 (srv): путь цены каждой сделки базы по ленте aggTrades — MFE/MAE от входа до выхода и 60 мин после, цена на горизонтах после входа,
NATR 15/30/60 (минутки, закрытая минута перед сигнальной, как `natr_of` движка), контекст BTC (ход за 1/4/24 ч до сигнала).
  python3 short_autopsy_path.py <base-trades.csv> <path.csv> [--side short|long] [--workers 3]
Нет решений и P&L-выводов: только описательные признаки. Лента читается только до 2026-11-14 00:00 UTC (17.11+ не читается; 14–16.11 тоже нет).
Знак: side=short — выгодное движение вниз; все расстояния в бп от цены входа (`entry_px_e8`)."""
import argparse
import csv
import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

DAY = 86_400_000
MIN = 60_000
END_MS = int(datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc).timestamp() * 1000)
POST_MS = 60 * MIN
HORIZONS = (1, 5, 15, 30, 60, 120, 240)
AGG_ROOTS = [("2026-06-17", "2026-07-17", "/data/p42-past06/aggtrades", "%s/%s-aggTrades-%s.zip"),
             ("2026-07-18", "2026-08-31", "/data/p42-past/aggtrades", "%s/%s-aggTrades-%s.zip"),
             ("2026-09-01", "2026-11-13", "/data/aggtrades/binance", "%s/%s-aggTrades-%s.zip")]
KL_ROOTS = [("2026-06-17", "2026-07-17", "/data/p42-past06/klines", "%s/%s-1m-%s.zip"),
            ("2026-07-18", "2026-08-31", "/data/p42-past/klines", "%s/%s-1m-%s.zip"),
            ("2026-09-01", "2026-11-13", "/data/klines/binance", "%s/%s.csv")]


def _path(roots, sym, date):
    for lo, hi, root, pat in roots:
        if lo <= date <= hi:
            return os.path.join(root, pat % ((sym, sym, date) if pat.count("%s") == 3 else (sym, date)))
    return None


def ticks(sym, lo_ms, hi_ms):
    """(t, p) сделки монеты в [lo_ms, hi_ms] — по суткам файлов, p в долях единицы цены."""
    ts, ps = [], []
    for d in range(lo_ms // DAY, min(hi_ms, END_MS - 1) // DAY + 1):
        date = datetime.datetime.fromtimestamp(d * DAY / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
        p = _path(AGG_ROOTS, sym, date)
        if p is None or not os.path.exists(p):
            continue
        df = pd.read_csv(p, usecols=[1, 5], header=0, dtype=np.float64)
        t = df.iloc[:, 1].to_numpy(dtype=np.int64)
        px = df.iloc[:, 0].to_numpy(dtype=np.float64)
        m = (t >= lo_ms) & (t <= hi_ms)
        ts.append(t[m])
        ps.append(px[m])
    if not ts:
        return np.zeros(0, np.int64), np.zeros(0)
    t, px = np.concatenate(ts), np.concatenate(ps)
    o = np.argsort(t, kind="stable")
    return t[o], px[o]


@lru_cache(maxsize=64)
def klines_day(sym, date):
    p = _path(KL_ROOTS, sym, date)
    if p is None or not os.path.exists(p):
        return None
    df = pd.read_csv(p, usecols=[0, 1, 2, 3, 4], header=0)
    return df.to_numpy(dtype=np.float64)   # open_time, o, h, l, c


def minute_bars(sym, end_ms, n):
    """n закрытых минуток, заканчивающихся перед минутой end_ms (не включая); нет данных → None."""
    first = end_ms - n * MIN
    rows = []
    for d in range(first // DAY, (end_ms - 1) // DAY + 1):
        date = datetime.datetime.fromtimestamp(d * DAY / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
        k = klines_day(sym, date)
        if k is None:
            continue
        rows.append(k)
    if not rows:
        return None
    k = np.concatenate(rows)
    k = k[(k[:, 0] >= first) & (k[:, 0] < end_ms)]
    return k


def natr(bars, p):
    """ATR/close, бп: простое среднее истинных диапазонов по последним p барам; закрытая последняя минутка."""
    if bars is None or len(bars) < p + 1:
        return float("nan")
    b = bars[-(p + 1):]
    h, l, c = b[1:, 2], b[1:, 3], b[1:, 4]
    pc = b[:-1, 4]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(tr.mean() / c[-1] * 1e4)


def one(r, side):
    s = -1.0 if side == "short" else 1.0
    ent, ex = int(r["entry_ms"]), int(r["exit_ms"])
    e0 = float(r["entry_px_e8"]) / 1e8
    out = dict(sym=r["sym"], entry_ms=ent, exit_ms=ex)
    hi = min(ex + POST_MS, END_MS - 1)
    t, p = ticks(r["sym"], ent, hi)
    if len(t) == 0:
        out["ok"] = 0
        return out
    out["ok"] = 1
    rel = s * (p / e0 - 1.0) * 1e4                       # бп в пользу стороны (шорт: цена вниз = плюс)
    inw = t <= ex
    if inw.any():
        w, tw = rel[inw], t[inw]
        out["mfe_bp"] = float(max(w.max(), 0.0))
        out["mae_bp"] = float(max(-w.min(), 0.0))
        out["t_mfe_min"] = float((tw[int(np.argmax(w))] - ent) / MIN)
        out["t_mae_min"] = float((tw[int(np.argmin(w))] - ent) / MIN)
        out["exit_rel_bp"] = float(w[-1])
    post = ~inw
    if post.any():
        out["post_mfe_bp"] = float(max(rel[post].max(), 0.0))
        out["post_mae_bp"] = float(max(-rel[post].min(), 0.0))
    for h in HORIZONS:
        m = t <= ent + h * MIN
        if t[-1] >= ent + h * MIN and m.any():
            out["r%d_bp" % h] = float(rel[m][-1])
            out["mfe%d_bp" % h] = float(max(rel[m].max(), 0.0))
            out["mae%d_bp" % h] = float(max(-rel[m].min(), 0.0))
    sig = int(r["sig_ms"]) // MIN * MIN
    bars = minute_bars(r["sym"], sig, 70)
    for pp in (15, 30, 60):
        out["natr%d_bp" % pp] = natr(bars, pp)
    for hname, hm in (("1h", 60), ("4h", 240), ("24h", 1440)):
        b = minute_bars("BTCUSDT", sig, hm + 1)
        out["btc_%s_bp" % hname] = float((b[-1, 4] / b[0, 4] - 1.0) * 1e4) if b is not None and len(b) >= hm * 0.9 else float("nan")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trades")
    ap.add_argument("out")
    ap.add_argument("--side", default="short")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.trades, encoding="utf-8")))
    rows.sort(key=lambda r: (r["sym"], int(r["entry_ms"])))
    with ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda r: one(r, a.side), rows))
    keys = []
    for r in res:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(res)
    print("rows", len(res), "no-ticks", sum(1 for r in res if not r.get("ok")))


if __name__ == "__main__":
    main()
