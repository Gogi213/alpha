"""П-42 §13.7: чёрный список аутсайдеров walk-forward → <OUT>/report-walkfwd.json, <OUT>/blacklist-<движок>-<сторона>-<окно>.csv и печать.

  P42_ROOT=data/p42v2 P42_OUT=data/p42v2-wf python tools/p42/walkfwd.py
На сутки D чёрный список — монеты, у которых по окну [max(0, D−w), D) (по суткам ВЫХОДА сделок) ≥ 5 клеток с её сделками и ≥ 90 % из них в минусе;
прогрев — сутки 0…14 без исключения. Исключается база (b0 минутки, b0_lx4 тики): приближённо, вычитанием P&L монеты за сутки D из суточного ряда.
Клетки: минутки — все варианты stage1/ext/joint/final; тики — 9 клеток tick-lx4 (регэксп как у outsiders.py).
"""
import csv
import glob
import json
import os
import re
import sys

os.environ.setdefault("P42_ROOT", "data/p42v2")
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

from lib import ND, ROOT, SIDES, read_coinday, read_trades, stat  # noqa: E402
from phase import rng  # noqa: E402

OUT = os.environ.get("P42_OUT") or os.path.join(ROOT, "..", "p42v2-wf")
M = json.loads(os.environ.get("P42_M", '{"long":227,"short":219}'))  # Холм по накопленному счётчику (§13.7)
MINC, SHARE, WARM = 5, 0.9, 15
WINDOWS = (("expanding", None), ("30", 30), ("7", 7), ("3", 3))
TICK_RE = r"^(b0|cA_|cB_|j_|v_).*lx4$"
ENGINES = {"1m": ([os.path.join(ROOT, d) for d in ("stage1", "ext", "joint", "final")], os.path.join(ROOT, "stage1", "b0"), None),
           "tick": ([os.path.join(ROOT, "tick-lx4")], os.path.join(ROOT, "tick-lx4", "b0_lx4"), TICK_RE)}
DAY0_MIN = 20160  # минута 01.09 → сутки 0 = 15.09


def cell_arrays(engine, side):
    """(coins, net[cell, coin, 60], cnt[cell, coin, 60]) по суткам выхода."""
    dirs, _, rx = ENGINES[engine]
    cells = []
    for r in dirs:
        for vdir in sorted(glob.glob(os.path.join(r, "*"))):
            if not os.path.exists(os.path.join(vdir, side + "-trades.csv")):
                continue
            if rx and not re.search(rx, os.path.basename(vdir)):
                continue
            tr = read_trades(vdir, side)
            if tr:
                cells.append((os.path.basename(vdir), tr))
    coins = sorted({t["sym"] for _, tr in cells for t in tr})
    ix = {c: i for i, c in enumerate(coins)}
    net = np.zeros((len(cells), len(coins), 60))
    cnt = np.zeros((len(cells), len(coins), 60), dtype=np.int32)
    for k, (_, tr) in enumerate(cells):
        for t in tr:
            d = (int(t["exit_min"]) - DAY0_MIN) // 1440
            d = min(max(d, 0), 59)
            net[k, ix[t["sym"]], d] += float(t["net"])
            cnt[k, ix[t["sym"]], d] += 1
    return coins, net, cnt, [n for n, _ in cells]


def blacklist(net, cnt, lo_fn):
    """BL[D] — булев вектор по монетам: монета в чёрном списке на сутки D (окно — по суткам выхода < D)."""
    ncell, ncoin, _ = net.shape
    cn, cc = np.cumsum(net, axis=2), np.cumsum(cnt, axis=2)
    bl = np.zeros((60, ncoin), dtype=bool)
    for D in range(WARM, 60):
        lo = lo_fn(D)
        hi = D  # сутки [lo, D)
        sn = cn[:, :, hi - 1] - (cn[:, :, lo - 1] if lo > 0 else 0)
        sc = cc[:, :, hi - 1] - (cc[:, :, lo - 1] if lo > 0 else 0)
        has = sc > 0
        n_has = has.sum(axis=0)
        n_neg = (has & (sn < 0)).sum(axis=0)
        bl[D] = (n_has >= MINC) & (n_neg >= SHARE * np.maximum(n_has, 1) - 1e-9)
    return bl


def daily(vdir, side):
    return np.array([float(r["pnl"]) for r in csv.DictReader(open(os.path.join(vdir, side + "-daily.csv")))])


def main():
    os.makedirs(OUT, exist_ok=True)
    r = rng()
    out = {"m": M, "warm": WARM, "res": {}}
    for eng, (dirs, bdir, rx) in ENGINES.items():
        for side in SIDES:
            z_h = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
            coins, net, cnt, names = cell_arrays(eng, side)
            ix = {c: i for i, c in enumerate(coins)}
            xb = daily(bdir, side)
            cd = read_coinday(bdir, side) or {}
            assert abs(sum(a.sum() for a in cd.values()) - xb.sum()) < 1e-3, (eng, side)
            trades = read_trades(bdir, side)
            for wname, w in WINDOWS:
                lo_fn = (lambda D: 0) if w is None else (lambda D, w=w: max(0, D - w))
                bl = blacklist(net, cnt, lo_fn)
                xe = xb.copy()
                nrem = np.zeros(60, dtype=int)
                rem_net = 0.0
                rem_tr = 0
                lst = []
                for D in range(60):
                    cs = [coins[i] for i in np.flatnonzero(bl[D])]
                    nrem[D] = len(cs)
                    for c in cs:
                        if c in cd:
                            xe[D] -= cd[c][D]
                            rem_net += cd[c][D]
                    sset = set(cs)
                    rem_tr += sum(1 for t in trades if int(t["entry_day"]) == D and t["sym"] in sset)
                    lst.append((D, len(cs), " ".join(cs)))
                with open(os.path.join(OUT, "blacklist-%s-%s-%s.csv" % (eng, side, wname)), "w", newline="", encoding="utf-8") as f:
                    cw = csv.writer(f)
                    cw.writerow(["day", "n", "coins"])
                    cw.writerows(lst)
                res = {"cells": len(names), "z_holm": z_h, "total_full": float(xb.sum()), "total_excl": float(xe.sum()), "removed_net": float(rem_net),
                       "trades_full": len(trades), "trades_removed": rem_tr,
                       "bl_mean": float(nrem[WARM:].mean()), "bl_min": int(nrem[WARM:].min()), "bl_max": int(nrem[WARM:].max()),
                       "coin_days": int(nrem.sum()), "coins_ever": int(bl.any(axis=0).sum()), "seg": {}}
                for lab, sl in (("A", slice(0, 30)), ("B", slice(30, 60))):
                    res["seg"][lab] = {"full": stat(xb[sl], r), "excl": stat(xe[sl], r), "delta": stat(xe[sl] - xb[sl], r)}
                for lab, sl in (("A'", slice(15, 30)), ("B", slice(30, 60)), ("15-59", slice(15, 60))):
                    d = stat(xe[sl] - xb[sl], r)
                    d["low_holm"], d["high_holm"] = d["mean"] - z_h * d["se"], d["mean"] + z_h * d["se"]
                    res["seg"]["Δ " + lab] = d
                    res["seg"]["full " + lab] = float(xb[sl].mean())
                    res["seg"]["excl " + lab] = float(xe[sl].mean())
                dall, da, db = res["seg"]["Δ 15-59"], res["seg"]["Δ A'"], res["seg"]["Δ B"]
                if dall["mean"] > 0 and dall["low_holm"] > 0 and da["mean"] > 0 and db["mean"] > 0:
                    res["reading"] = "помогает"
                elif dall["mean"] < 0 and dall["hi"] < 0:
                    res["reading"] = "вредит"
                else:
                    res["reading"] = "в шуме"
                out["res"]["%s|%s|%s" % (eng, side, wname)] = res
                a, b = res["seg"]["A"], res["seg"]["B"]
                print("%-4s %-5s %-9s cells=%3d BL %4.1f [%d;%d] | A %+7.2f→%+7.2f | B %+7.2f→%+7.2f | Δ15-59 %+6.2f [%6.2f;%6.2f] Holm-low %+6.2f | Δ A' %+6.2f B %+6.2f | tot %+8.1f→%+8.1f tr %d→-%d | %s" % (
                    eng, side, wname, len(names), res["bl_mean"], res["bl_min"], res["bl_max"], a["full"]["mean"], a["excl"]["mean"], b["full"]["mean"],
                    b["excl"]["mean"], dall["mean"], dall["lo"], dall["hi"], dall["low_holm"], da["mean"], db["mean"], res["total_full"],
                    res["total_excl"], res["trades_full"], rem_tr, res["reading"]))
    json.dump(out, open(os.path.join(OUT, "report-walkfwd.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
