#!/usr/bin/env python3
"""TK-493 F6, G7: единицы и знак фандинга по BTC (и ETH, SOL) на 9 биржах, из сырья `<root>/<биржа>/funding/<SYM>.csv.gz`.

Что считается, на каждый контракт:
  * число выплат, первая/последняя, интервал по разности соседних выплат (медиана, доля выплат с этим интервалом) против
    столбца interval_h файла;
  * ставка за выплату: среднее, медиана, мин, макс (bps) — единицы: «доля за выплату» (0,0001 = 1 bps);
  * годовая ставка, % = среднее по выплатам × (8760 / интервал, ч) × 100 — сопоставима между биржами, если единицы и интервалы верны
    (ошибка единиц даёт ×100 или ×10⁴, ошибка интервала — кратность 8, 4, 1);
  * суммы по 8-часовым блокам UTC (00-08-16; выплата в момент границы относится к блоку, который она закрывает;
    допуск 60 с на метку после границы): корреляция Пирсона и наклон (через ноль) к ряду Binance — знак и масштаб относительно
    биржи, у которой знак задокументирован («ставка > 0: лонги платят шортам»).
Только стандартная библиотека. Выход: таблица в stdout и csv (--out).
"""
import argparse
import csv
import gzip
import os
import statistics as st
from math import sqrt

BLOCK_MS = 8 * 3600_000
SYMS = {
    "binance": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"},
    "bybit": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"},
    "okx": {"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP", "SOL": "SOL-USDT-SWAP"},
    "bingx": {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT"},
    "bitget": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"},
    "kraken": {"BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD"},
    "aster": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"},
    "lighter": {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"},
    "hyperliquid": {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"},
}
ORDER = ["binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter", "hyperliquid"]


def read(root, ex, sym):
    rows = []
    with gzip.open(os.path.join(root, ex, "funding", sym + ".csv.gz"), "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            fs = line.strip().split(",")
            if len(fs) >= 2 and fs[0]:
                rows.append((int(fs[0]), float(fs[1]), fs[2] if len(fs) > 2 else ""))
    return rows


def blocks(rows):
    b = {}
    for t, r, _ in rows:
        k = (t - 60_000) // BLOCK_MS
        b[k] = b.get(k, 0.0) + r
    return b


def pearson(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--assets", default="BTC,ETH,SOL")
    a = ap.parse_args()
    out = []
    for asset in a.assets.split(","):
        data = {}
        for ex in ORDER:
            sym = SYMS[ex][asset]
            try:
                rows = read(a.root, ex, sym)
            except FileNotFoundError:
                print(f"{asset} {ex}: нет файла {sym}")
                continue
            data[ex] = (sym, rows)
        ref = blocks(data["binance"][1])
        for ex in ORDER:
            if ex not in data:
                continue
            sym, rows = data[ex]
            rates = [r for _, r, _ in rows]
            dts = [(rows[i][0] - rows[i - 1][0]) / 3600_000 for i in range(1, len(rows))]
            med = st.median(dts)
            share = sum(1 for d in dts if abs(d - med) < 0.02) / len(dts)
            ih = {}
            for _, _, h in rows:
                ih[h] = ih.get(h, 0) + 1
            ihs = "/".join(f"{k}:{v}" for k, v in sorted(ih.items()))
            ih_ok = sum(1 for i in range(1, len(rows)) if rows[i][2] != "" and abs(float(rows[i][2]) - dts[i - 1]) < 0.02) / len(dts)
            mean = sum(rates) / len(rates)
            ann = mean * (8760.0 / med) * 100.0
            bl = blocks(rows)
            ks = sorted(set(bl) & set(ref))
            xs = [ref[k] for k in ks]
            ys = [bl[k] for k in ks]
            corr = pearson(xs, ys)
            slope = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs) if sum(x * x for x in xs) > 0 else float("nan")
            sgn = "+" if corr > 0.3 else ("-" if corr < -0.3 else "0")
            out.append(dict(asset=asset, exchange=ex, symbol=sym, payments=len(rows),
                            first_utc=_t(rows[0][0]), last_utc=_t(rows[-1][0]), interval_h_median=round(med, 4),
                            share_at_median=round(share, 3), interval_h_column=ihs, column_matches_spacing=round(ih_ok, 3),
                            mean_bps=round(mean * 1e4, 4), median_bps=round(st.median(rates) * 1e4, 4),
                            min_bps=round(min(rates) * 1e4, 3), max_bps=round(max(rates) * 1e4, 3),
                            annual_pct=round(ann, 2), blocks=len(ks), corr_binance_8h=round(corr, 3),
                            slope_vs_binance=round(slope, 3), sign_vs_binance=sgn))
    cols = list(out[0].keys())
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    hdr = f"{'актив':5} {'биржа':12} {'выплат':>6} {'инт.ч':>6} {'доля':>5} {'interval_h (файл)':>18} {'среднее,bps':>11} {'мин,bps':>9} {'макс,bps':>9} {'год,%':>8} {'corr8h':>7} {'наклон':>7}"
    print(hdr)
    for r in out:
        print(f"{r['asset']:5} {r['exchange']:12} {r['payments']:>6} {r['interval_h_median']:>6} {r['share_at_median']:>5} {r['interval_h_column']:>18} "
              f"{r['mean_bps']:>11} {r['min_bps']:>9} {r['max_bps']:>9} {r['annual_pct']:>8} {r['corr_binance_8h']:>7} {r['slope_vs_binance']:>7}")


def _t(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
