#!/usr/bin/env python3
"""TK-493 F7 (ПК, stdlib+numpy): параметры клеток D2 по фандингу ФОРМИРОВАНИЯ (П-37 §6.3, Н21). Платежи с time_ms < 15.10 00:00 UTC.

  k            - из {1, 3, 6}: минимум средней квадратичной ошибки прогноза следующей выплаты ноги Y трейлинг-средним k прошлых выплат
                 (пул по ногам Y клетки, каждая нога один раз);
  hold_payments- медианная длина эпизодов постоянного знака разности ставок (Y минус X, обе приведены к ставке в час: rate / interval_h)
                 в выплатах ноги Y (пул эпизодов клетки), не меньше 1;
  max_hold     - П-37 не задаёт для D2: НАЗНАЧЕНО без Судьи, подтвердить: hold_payments x медианный interval_h ноги Y x 60 минут
                 (буквальное 4 x полураспад спреда цен - минуты - не даёт держать позицию через выплату);
  window, min_obs, refresh - ядром при правиле funding не используются; прогрев: window = min_obs = 60 минут (НАЗНАЧЕНО без Судьи).
Единицы и знак ставок не сверены (G7, F6): годовая ставка BTC по биржам выводится справочно в g7_btc_annual.csv.
"""
import csv, gzip, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

GRID0_MS = 1_577_836_800_000
CUT_MS = GRID0_MS + S.D_TRADE0 * S.MIN_DAY * 60_000
KS = (1, 3, 6)
FUND = os.path.join(S.SEL, "funding")


def load_funding(ex, stem):
    p = os.path.join(FUND, ex, stem + ".csv.gz")
    if not os.path.exists(p):
        return None
    t, r, h = [], [], []
    with gzip.open(p, "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for row in rd:
            ms = int(row[0])
            if ms >= CUT_MS:
                break
            t.append(ms); r.append(float(row[1])); h.append(float(row[2]))
    return np.array(t), np.array(r), np.array(h)


def mse_k(r, k):
    if len(r) <= k:
        return 0.0, 0
    c = np.cumsum(np.insert(r, 0, 0.0))
    mean_prev = (c[k:-1] - c[:-k - 1]) / k          # среднее r[j-k..j-1] для j = k..n-1
    e = r[k:] - mean_prev
    return float((e * e).sum()), len(e)


def episodes(ty, ry, hy, tx, rx, hx):
    """Длины (в выплатах Y) эпизодов постоянного знака d = ry/hy - rx/hx; ставка X - последняя выплата до момента выплаты Y."""
    if len(tx) == 0 or len(ty) == 0:
        return []
    idx = np.searchsorted(tx, ty, side="right") - 1
    ok = idx >= 0
    if ok.sum() < 2:
        return []
    d = (ry / hy)[ok] - (rx / hx)[idx[ok]]
    sg = np.sign(d)
    sg = sg[sg != 0]
    out, run = [], 1
    for i in range(1, len(sg)):
        if sg[i] == sg[i - 1]:
            run += 1
        else:
            out.append(run); run = 1
    if len(sg):
        out.append(run)
    return out


def main():
    cells = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    res, excl = [], []
    cache = {}
    for cid in sorted(cells):
        if cells[cid]["kind"] != "D2":
            continue
        pr = S.read_csv(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".csv"))
        sse = {k: [0.0, 0] for k in KS}
        eps, hy_all, used, seen_y = [], [], 0, set()
        keep = []
        for p in pr:
            ky, kx = (p["ex_y"], p["y"]), (p["ex_x"], p["x"])
            for k_ in (ky, kx):
                if k_ not in cache:
                    cache[k_] = load_funding(*k_)
            fy, fx = cache[ky], cache[kx]
            why = None
            if fy is None or fx is None:
                why = "нет файла фандинга"
            elif len(fy[0]) < max(KS) + 1 or len(fx[0]) < 2:
                why = "выплат на формировании меньше %d (Y) или 2 (X)" % (max(KS) + 1)
            if why:
                excl.append([cid, p["ex_y"], p["y"], p["ex_x"], p["x"], why])
                continue
            keep.append(p)
            used += 1
            eps.extend(episodes(fy[0], fy[1], fy[2], fx[0], fx[1], fx[2]))
            if ky not in seen_y:
                seen_y.add(ky)
                hy_all.append(float(np.median(fy[2])))
                for k in KS:
                    s, n = mse_k(fy[1], k)
                    sse[k][0] += s; sse[k][1] += n
        if not used:
            res.append([cid, len(pr), 0, "", "", "", "", "", "нет пар с фандингом обеих ног"])
            continue
        mse = {k: sse[k][0] / sse[k][1] for k in KS}
        kbest = min(KS, key=lambda k: (mse[k], k))
        hold = max(1, int(round(float(np.median(eps))))) if eps else 1
        ih = float(np.median(hy_all))
        mh = int(round(hold * ih * 60))
        res.append([cid, len(pr), used, kbest, hold, "%g" % ih, mh, "|".join("%g" % mse[k] for k in KS), "параметры измерены"])
        S.write_csv(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".d2ok.csv"),
                    ["ex_y", "y", "ex_x", "x"], [[p["ex_y"], p["y"], p["ex_x"], p["x"]] for p in keep])
    S.write_csv(os.path.join(S.DOCS, "d2_params.csv"),
                ["cell", "pairs", "pairs_with_funding", "k", "hold_payments", "interval_h_y_median", "max_hold_min", "mse_k1|k3|k6", "status"], res)
    S.write_csv(os.path.join(S.DOCS, "d2_excluded.csv"), ["cell", "ex_y", "y", "ex_x", "x", "reason"], excl)
    # справочно G7: годовая ставка BTC на формировании по биржам = mean(rate / interval_h) * 8760
    btc = {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "aster": "BTCUSDT",
           "kraken": "PF_XBTUSD", "lighter": "BTC", "bitget": "BTCUSDT"}
    g7 = []
    for ex, sym in btc.items():
        f = load_funding(ex, S.safe(sym))
        if f is None or len(f[0]) == 0:
            g7.append([ex, sym, "", "", "", ""])
            continue
        t, r, h = f
        g7.append([ex, sym, len(t), "%g" % np.median(h), "%.4f" % (np.mean(r / h) * 8760), "%.2f" % (np.mean(r) * 1e4)])
    S.write_csv(os.path.join(S.DOCS, "g7_btc_annual.csv"), ["exchange", "symbol", "payments", "interval_h_median", "annual_rate_mean", "mean_rate_bps_per_payment"], g7)
    print("D2 клеток", len(res), "исключено пар", len(excl))


if __name__ == "__main__":
    main()
