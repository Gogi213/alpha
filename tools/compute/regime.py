#!/usr/bin/env python3
"""Режим рынка по минутам (S3 плана по сторонам, 2026-09-20): медиана хода монет пула за прошлый час
и за 4 часа на каждую минуту суток — из минутных рядов середины `study/touches/<сутки>/mids1m-*.csv`
(`lob touches`, S2) — плюс те же ходы битка и эфира из справочных свечей `study/regime/ref-*-1m.csv`
(`ref-klines.py`). Ось этапа 2 дороги: «направление за час / 4 часа» [D 10:17; S 54:00].

    python3 tools/compute/regime.py --day 2026-09-17 [--touches study/touches] [--regime-dir study/regime]

Пишет `study/regime/<сутки>.csv`: `minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,
btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps` — ход **к началу** минуты (закрытия предыдущих минут, без
заглядывания внутрь минуты касания) (пусто — нет данных: у пула первые T минут суток без
`ret_T` — ряды суточные; у BTC/ETH предыдущие сутки есть) и обновляет `study/regime/days.csv`
(`day_utc,pool_day_ret_pct,pool_coins,pool_up,btc_day_ret_pct,eth_day_ret_pct` — дневная строка:
закрытие последней минуты к закрытию первой; медиана по монетам). Ничего не назначает: пороги
режима титруются сеткой по квартилям этих колонок.
"""
import argparse
import csv
import glob
import os
import statistics
import sys

MINUTE_MS = 60_000
WINDOWS = {"1h": 60, "4h": 240}


def load_minutes(path, key="mid2x"):
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[int(r["minute_ms"])] = float(r[key])
    return out


def ret_bps(series, minute, back):
    """Ход к минуте `minute` **без заглядывания**: от закрытия минуты `minute − back − 1` к закрытию
    минуты `minute − 1` — обе закрыты к началу `minute` (касание внутри минуты видит только прошлое;
    первая версия 20.09 брала закрытие самой минуты — до 59 с будущего, исправлено тем же днём)."""
    now = series.get(minute - MINUTE_MS)
    then = series.get(minute - (back + 1) * MINUTE_MS)
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1.0) * 1e4


def fmt(v):
    return "" if v is None else f"{v:.4f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--day", required=True, help="сутки UTC, YYYY-MM-DD")
    p.add_argument("--touches", default="study/touches")
    p.add_argument("--regime-dir", default="study/regime")
    a = p.parse_args()
    os.makedirs(a.regime_dir, exist_ok=True)
    coins = {}
    for path in sorted(glob.glob(os.path.join(a.touches, a.day, "mids1m-*.csv"))):
        sym = os.path.basename(path)[len("mids1m-"):-len(".csv")]
        s = load_minutes(path)
        if s:
            coins[sym] = s
    if not coins:
        raise SystemExit(f"{a.day}: нет mids1m-*.csv в {os.path.join(a.touches, a.day)} — сначала касания (S2)")
    refs = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        path = os.path.join(a.regime_dir, f"ref-{sym}-1m.csv")
        refs[sym] = load_minutes(path, "close") if os.path.exists(path) else {}
    first = min(min(s) for s in coins.values())
    last = max(max(s) for s in coins.values())
    day_start = first - first % 86_400_000
    out_path = os.path.join(a.regime_dir, f"{a.day}.csv")
    n_rows = 0
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute_ms", "pool_ret_1h_bps", "pool_ret_4h_bps", "n_coins",
                    "btc_ret_1h_bps", "btc_ret_4h_bps", "eth_ret_1h_bps", "eth_ret_4h_bps"])
        minute = day_start
        while minute <= last:
            row = [minute]
            n_coins = 0
            for name, back in WINDOWS.items():
                vals = [v for v in (ret_bps(s, minute, back) for s in coins.values()) if v is not None]
                row.append(fmt(statistics.median(vals)) if vals else "")
                if name == "1h":
                    n_coins = len(vals)
            row.append(n_coins)
            for sym in ("BTCUSDT", "ETHUSDT"):
                for back in WINDOWS.values():
                    row.append(fmt(ret_bps(refs[sym], minute, back)))
            w.writerow(row)
            n_rows += 1
            minute += MINUTE_MS
    # дневная строка
    day_rets = []
    for s in coins.values():
        ks = sorted(s)
        if ks and s[ks[0]] > 0 and ks[-1] - ks[0] >= 6 * 60 * MINUTE_MS:
            day_rets.append(s[ks[-1]] / s[ks[0]] - 1.0)
    day_end = day_start + 86_400_000 - MINUTE_MS

    def ref_day(sym):
        r = refs[sym]
        a0, a1 = r.get(day_start), r.get(day_end)
        return None if a0 is None or a1 is None or a0 <= 0 else (a1 / a0 - 1.0) * 100

    days_path = os.path.join(a.regime_dir, "days.csv")
    rows = {}
    if os.path.exists(days_path):
        with open(days_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["day_utc"]] = r
    rows[a.day] = {
        "day_utc": a.day,
        "pool_day_ret_pct": fmt(statistics.median(day_rets) * 100) if day_rets else "",
        "pool_coins": len(day_rets),
        "pool_up": sum(1 for x in day_rets if x > 0),
        "btc_day_ret_pct": fmt(ref_day("BTCUSDT")),
        "eth_day_ret_pct": fmt(ref_day("ETHUSDT")),
    }
    with open(days_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[a.day].keys()))
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    r = rows[a.day]
    print(f"{a.day}: минут {n_rows}, монет {len(coins)}, пул за день {r['pool_day_ret_pct'] or '—'} % "
          f"({r['pool_up']}/{r['pool_coins']} вверх), BTC {r['btc_day_ret_pct'] or '—'} %, "
          f"ETH {r['eth_day_ret_pct'] or '—'} % → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
