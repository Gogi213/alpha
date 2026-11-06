#!/usr/bin/env python3
"""TK-493 F6, G7: расписать «руками» одну сделку D2 по сырью — каждую выплату фандинга внутри держания, цены, количества, итог.
  g7_hand.py --root ~/xdl/out --trades T.csv --row N --y-ex binance --y BTCUSDT --x-ex bybit --x BTCUSDT
Выводит строки выплат обеих ног (минута, время UTC, ставка из файла, open, вклад в bps номинала Y) и сумму; сверяет с csv.
"""
import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_trades import G0_MS, MIN_MS, Leg, price_at  # noqa: E402


def utc(m):
    return datetime.datetime.fromtimestamp((G0_MS + m * MIN_MS) / 1000, datetime.timezone.utc).strftime("%d.%m %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--row", type=int, required=True)
    ap.add_argument("--y-ex", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--x-ex", required=True)
    ap.add_argument("--x", required=True)
    a = ap.parse_args()
    r = list(csv.DictReader(open(a.trades, encoding="utf-8")))[a.row]
    d, fin, fout = int(r["dir"]), int(r["fill_in"]), int(r["fill_out"])
    Y, X = Leg(a.root, a.y_ex, a.y), Leg(a.root, a.x_ex, a.x)
    oy_in, ox_in = Y.open[fin], X.open[fin]
    qy, qx = d / oy_in, -d * 1.0 / ox_in
    print(f"сделка #{a.row}: dir={d:+d}, вход (исполнение) {utc(fin)} [мин {fin}], выход {utc(fout)} [мин {fout}]; β=1")
    print(f"  open входа: Y {oy_in!r}, X {ox_in!r}; количества qY = dir/openY = {qy!r}, qX = −dir/openX = {qx!r}")
    print(f"  open выхода: Y {Y.open[fout]!r}, X {X.open[fout]!r}")
    gross = 1e4 * (qy * (Y.open[fout] - oy_in) + qx * (X.open[fout] - ox_in))
    fee = 3.15 * (abs(qy) * (oy_in + Y.open[fout]) + abs(qx) * (ox_in + X.open[fout]))
    print(f"  валовый = 1e4·(qY·ΔY + qX·ΔX) = {gross:.6f} bps; комиссия = 3,15·(|qY|·(Pвх+Pвых) + |qX|·(Pвх+Pвых)) = {fee:.6f} bps")
    tot = 0.0
    print("  выплаты строго внутри (вход, выход) — минута, UTC, нога, ставка из файла, цена (open или последний известный), вклад = −q·цена·ставка·1e4:")
    for name, leg, q in (("Y", Y, qy), ("X", X, qx)):
        for m, rate in leg.fund:
            if fin < m < fout:
                px = leg.open.get(m)
                tag = ""
                if px is None:
                    j = m
                    while j not in leg.open:
                        j -= 1
                    px, tag = leg.open[j], f" (open минуты нет, последний известный — {utc(j)})"
                c = -q * px * rate * 1e4
                tot += c
                print(f"    {m} {utc(m)} {name} ставка {rate:+.8f} цена {px!r}{tag} → {c:+.6f}")
    print(f"  фандинг руками {tot:+.6f} bps; csv {float(r['funding_bps']):+.6f}; net руками {gross - fee + tot:.6f}, csv {float(r['net_bps']):.6f}; max|Δ| = "
          f"{max(abs(gross - float(r['gross_bps'])), abs(fee - float(r['fee_bps'])), abs(tot - float(r['funding_bps'])), abs(gross - fee + tot - float(r['net_bps']))):.2e}")


if __name__ == "__main__":
    main()
