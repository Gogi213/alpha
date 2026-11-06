#!/usr/bin/env python3
"""TK-493 F6, G7 (добавка): сверка единиц фандинга по ВСЕМ файлам ставок каждой биржи, не только по BTC/ETH/SOL.
По каждому файлу: число выплат, медианный интервал по разности соседних выплат (ч), среднее за выплату (bps), годовая ставка, %
(= среднее × 8760 / интервал × 100), максимум |ставка| (bps). По бирже: медиана, 5-й и 95-й процентили годовой ставки по контрактам,
доля контрактов с |годовая| > 300 %, доля контрактов, у которых столбец interval_h не равен измеренному интервалу, максимум |ставка|.
Ошибка единиц (проценты вместо долей, bps вместо долей) дала бы смещение медианы на два-четыре порядка у всей биржи.
Только стандартная библиотека. Использование: g7_all_contracts.py --root ~/xdl/out --out summary.csv [--files files.csv]
"""
import argparse
import csv
import gzip
import os
import statistics as st

EX = ["binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter", "hyperliquid"]


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--files")
    a = ap.parse_args()
    summ, allrows = [], []
    for ex in EX:
        d = os.path.join(a.root, ex, "funding")
        ann, mism, mx, nfile, nrows, big, few = [], 0, 0.0, 0, 0, 0, 0
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".csv.gz"):
                continue
            rows = []
            with gzip.open(os.path.join(d, fn), "rt", encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    fs = line.strip().split(",")
                    if len(fs) >= 2 and fs[0]:
                        rows.append((int(fs[0]), float(fs[1]), fs[2] if len(fs) > 2 else ""))
            nfile += 1
            nrows += len(rows)
            if len(rows) < 10:
                few += 1
                continue
            dts = [(rows[i][0] - rows[i - 1][0]) / 3600_000 for i in range(1, len(rows))]
            med = st.median(dts)
            mean = sum(r for _, r, _ in rows) / len(rows)
            colv = [r[2] for r in rows if r[2] != ""]
            col = float(st.mode(colv)) if colv else float("nan")
            bad = col == col and abs(col - med) > 0.05 and abs(col - round(med)) > 0.05
            mism += bad
            m = max(abs(r) for _, r, _ in rows) * 1e4
            mx = max(mx, m)
            an = mean * 8760.0 / med * 100.0
            ann.append(an)
            big += abs(an) > 300
            allrows.append((ex, fn[:-7], len(rows), round(med, 3), col, round(mean * 1e4, 4), round(an, 2), round(m, 2)))
        if ann:
            summ.append(dict(exchange=ex, files=nfile, files_used=len(ann), files_lt10_payments=few, payments=nrows,
                             annual_median_pct=round(st.median(ann), 2), annual_p05_pct=round(q(ann, 0.05), 2), annual_p95_pct=round(q(ann, 0.95), 2),
                             share_abs_annual_gt300=round(big / len(ann), 4), share_interval_column_mismatch=round(mism / len(ann), 4),
                             max_abs_rate_bps=round(mx, 2)))
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0].keys()))
        w.writeheader()
        w.writerows(summ)
    if a.files:
        with open(a.files, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["exchange", "symbol", "payments", "interval_h_measured", "interval_h_column", "mean_bps", "annual_pct", "max_abs_bps"])
            w.writerows(allrows)
    print(f"{'биржа':12} {'файлов':>6} {'выплат':>8} {'год.мед,%':>9} {'p05':>8} {'p95':>8} {'|год|>300%':>10} {'interval≠':>9} {'max|r|,bps':>10}")
    for s in summ:
        print(f"{s['exchange']:12} {s['files_used']:>6} {s['payments']:>8} {s['annual_median_pct']:>9} {s['annual_p05_pct']:>8} {s['annual_p95_pct']:>8} "
              f"{s['share_abs_annual_gt300']:>10} {s['share_interval_column_mismatch']:>9} {s['max_abs_rate_bps']:>10}")


if __name__ == "__main__":
    main()
