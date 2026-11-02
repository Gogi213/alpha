#!/usr/bin/env python3
"""Ручная сверка сделок `alpha statarb run` по сырым свечам (TK-484, §11 п.2 «3 сделки»). Только stdlib, пары (K=1).

Независимо от ядра берёт из csv.gz свечей: open минут исполнения (вход = сигнал+1, выход = сигнал+1), цены Y и X,
по ним восстанавливает β из `gross_bps` и проверяет `fee_bps` по формуле ядра с комиссией тейкера
(`--taker-fee-bps`, из src/lob/costs.rs: 3.15). Дополнительно считает свежий OLS β и z на минуте сигнала по
окну `--window` (ядро обновляет β раз в `refresh` минут, поэтому расхождение β допустимо — печатается).
Сверка арифметики сделки и времени исполнения; логику сигнала проверяют reference-тесты ядра (TK-479).
"""
import argparse
import csv
import gzip
import math

EPOCH_MIN = 1577836800000 // 60000  # 2020-01-01 UTC в минутах от 1970


def load(path, lo, hi):
    """{минута_от_2020: (open, close)} для минут [lo, hi]."""
    out = {}
    with gzip.open(path, "rt") as f:
        next(f)
        for line in f:
            f_ = line.split(",")
            m = int(f_[0]) // 60000 - EPOCH_MIN
            if lo <= m <= hi:
                out[m] = (float(f_[1]), float(f_[4]))
            elif m > hi:
                break
    return out


def ols(ly, lx):
    n = len(ly)
    mx, my = sum(lx) / n, sum(ly) / n
    sxx = sum((x - mx) ** 2 for x in lx)
    b = sum((x - mx) * (y - my) for x, y in zip(lx, ly)) / sxx
    a = my - b * mx
    return a, b


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trades", required=True)
    p.add_argument("--candles", required=True, help="каталог <SYM>.csv.gz биржи")
    p.add_argument("--window", type=int, required=True)
    p.add_argument("--taker-fee-bps", type=float, required=True)
    p.add_argument("--pick", type=int, nargs="+", required=True, help="номера строк trades csv (с 0, без заголовка)")
    a = p.parse_args()
    rows = list(csv.DictReader(open(a.trades)))
    fee = a.taker_fee_bps
    lo_all = min(int(rows[i]["sig_in"]) for i in a.pick) - a.window
    hi_all = max(int(rows[i]["fill_out"]) for i in a.pick)
    syms = {rows[i][k] for i in a.pick for k in ("y", "x")}
    cache = {s_: load(f"{a.candles}/{s_}.csv.gz", lo_all, hi_all) for s_ in sorted(syms)}  # один проход по символу
    for i in a.pick:
        r = rows[i]
        d = int(r["dir"])
        si, fi, so, fo = (int(r[k]) for k in ("sig_in", "fill_in", "sig_out", "fill_out"))
        cy, cx = cache[r["y"]], cache[r["x"]]
        yi, yo = cy[fi][0], cy[fo][0]
        xi, xo = cx[fi][0], cx[fo][0]
        beta = ((yo / yi - 1) - float(r["gross_bps"]) / 1e4 / d) / (xo / xi - 1)
        fee_hand = fee * (1 + yo / yi) + beta * fee * (1 + xo / xi)
        gross_hand = 1e4 * d * ((yo / yi - 1) - beta * (xo / xi - 1))
        net_hand = gross_hand - fee_hand + float(r["funding_bps"])
        ms = [m for m in range(si - a.window + 1, si + 1) if m in cy and m in cx]
        ly = [math.log(cy[m][1]) for m in ms]
        lx = [math.log(cx[m][1]) for m in ms]
        al, be = ols(ly, lx)
        sp = [y - al - be * x for y, x in zip(ly, lx)]
        mu = sum(sp) / len(sp)
        sd = math.sqrt(sum((s - mu) ** 2 for s in sp) / (len(sp) - 1))
        print(f"сделка #{i}: {r['y']}/{r['x']} dir={d} сигнал {si} → вход {fi} (+{fi - si}), сигнал {so} → выход {fo} (+{fo - so})")
        print(f"  open Y {yi}→{yo}, X {xi}→{xo}; β из gross = {beta:.4f}, свежий OLS β = {be:.4f}, z(сигнал, свежий OLS) = {(sp[-1] - mu) / sd:.2f}")
        print(f"  gross  ядро {float(r['gross_bps']):.4f}  руками {gross_hand:.4f}")
        print(f"  fee    ядро {float(r['fee_bps']):.4f}  руками {fee_hand:.4f}")
        print(f"  net    ядро {float(r['net_bps']):.4f}  руками {net_hand:.4f}  Δ {float(r['net_bps']) - net_hand:+.2e}")


if __name__ == "__main__":
    main()
