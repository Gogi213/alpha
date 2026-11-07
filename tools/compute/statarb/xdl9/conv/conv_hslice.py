#!/usr/bin/env python3
"""TK-493, П-38 r2 §10.7 (описательно, на формировании): срезы «подсказки видео» - значения связок H1 (HK-акции Binance x Bitget), H2 BRETT, H3 LAB, H4 TA, H6 LPT.

Для каждой связки печатается:
  (1) членство в универсуме C2 (c1d2 = 1) и рынок; если нет - причина (вердикт R2' из asset_pairs.csv);
  (2) допуск по полураспаду Q5 (по q5_full.json): HL15, верхняя граница, для T = 2/6/24 ч;
  (3) значения C2 по сетке 180 комбинаций из grid_full: сделки, средний валовый и чистый на сделку при +1 мин, доля <= 2 мин - на выбранной комбинации класса и на опорных точках
      (theta = 25,2/40/80/160/320 при k = 3, p = 1, T = 6 ч, s_stop = 2);
  (4) правило «как в видео» (conv_hints.pair_table: относительное rel и абсолютное abs отклонение, theta = 10..160) на панелях формирования - строки W2 (описательно).
Ничего не выбирается; окно (сутки >= 2479) не читается (conv_guard).
  python conv_hslice.py --out OUT --last-day 2478 --classes crypto,eq
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_agg as AG       # noqa: E402
import conv_grid as GR      # noqa: E402
import conv_guard as G      # noqa: E402
import conv_hints as HI     # noqa: E402
import conv_world as W      # noqa: E402

FI = GR.FI
HINTS = [
    ("H2 BRETT BingX x Bitget", "bingx", "BRETT-USDT", "bitget", "BRETTUSDT"),
    ("H3 LAB Binance x Aster", "binance", "LABUSDT", "aster", "LABUSDT"),
    ("H4a TA Binance x Aster", "binance", "TAUSDT", "aster", "TAUSDT"),
    ("H4b TA Binance x BingX", "binance", "TAUSDT", "bingx", "TA-USDT"),
    ("H4c TA Binance x Gate", "binance", "TAUSDT", "gate", "TA_USDT"),
    ("H6 LPT Binance x BingX", "binance", "LPTUSDT", "bingx", "LPT-USDT"),
    ("H1 MINIMAX Binance x Bitget HKD", "binance", "MINIMAXUSDT", "bitget", "MINIMAXHKDUSDT"),
    ("H1 MINIMAX Binance x Bitget USDT", "binance", "MINIMAXUSDT", "bitget", "MINIMAXUSDT"),
    ("H1 ZHIPU Binance x Bitget HKD", "binance", "ZHIPUUSDT", "bitget", "ZHIPUHKDUSDT"),
    ("H1 ZHIPU Binance x Bitget USDT", "binance", "ZHIPUUSDT", "bitget", "ZHIPUUSDT"),
    ("H1 TENCENT HK0700 x Bitget HKD", "binance", "HK0700USDT", "bitget", "TENCENTHKDUSDT"),
    ("H1 TENCENT HK0700 x Bitget USDT", "binance", "HK0700USDT", "bitget", "TENCENTUSDT"),
    ("H1 LENOVO HK0992 x Bitget HKD", "binance", "HK0992USDT", "bitget", "LENOVOHKDUSDT"),
    ("H1 XIAOMI HK1810 x Bitget HKD", "binance", "HK1810USDT", "bitget", "XIAOMIHKDUSDT"),
    ("H1 XIAOMI HK1810 x Bitget USDT", "binance", "HK1810USDT", "bitget", "XIAOMIUSDT"),
    ("H1 SHEIN HK0625 x Bitget HKD", "binance", "HK0625USDT", "bitget", "SHEINHKDUSDT"),
    ("H1 SHEIN HK0625 x Bitget USDT", "binance", "HK0625USDT", "bitget", "SHEINUSDT"),
]
REF_POINTS = [(25.2, 3, 1, 360, 2.0), (40.0, 3, 1, 360, 2.0), (80.0, 3, 1, 360, 2.0), (160.0, 3, 1, 360, 2.0), (320.0, 3, 1, 360, 2.0)]


def norm_key(ex1, s1, ex2, s2):
    a, b = (ex1, s1), (ex2, s2)
    return (a, b) if a <= b else (b, a)


def per_trade(gr, ci, row):
    t = gr.tab[ci][row]
    n = float(t[:, FI["n"]].sum())
    if not n:
        return dict(trades=0, gross=None, net=None, fund=None, le2=None)
    return dict(trades=int(n), gross=float(t[:, FI["gross"]].sum() / n), net=float(t[:, FI["net"]].sum() / n), fund=float(t[:, FI["fund"]].sum() / n),
                le2=float(t[:, FI["n_le2"]].sum() / n))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--classes", default="crypto,eq")
    ap.add_argument("--last-day", type=int, default=2478)
    ap.add_argument("--L", type=int, default=1440)
    ap.add_argument("--res", default=None)
    W.add_gate_args(ap)
    a = ap.parse_args(argv)
    W.apply_gate_args(a)
    script = os.path.join(HERE, "conv_run.py")
    params = (os.path.join(HERE, "P-38-params-2026-11-07.csv"), None)
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    G.check_days(a.last_day, what="hslice")
    G.require_k0()
    G.ensure_params_committed()
    world = W.World(G.HOME)
    U = world.universe()
    byid = {(p["exA"], p["symA"], p["exB"], p["symB"]): p for p in U}
    allrows = world.pair_table_rows()                 # asset_pairs.csv (8 бирж) + таблица пар Gate, если Gate включён (G1)
    verd = {}
    for r in allrows:
        verd[norm_key(r["ex1"], r["sym1"], r["ex2"], r["sym2"])] = (r["verdict"], r["c1d2"], r["why"])
    grids = {}
    sel = {}
    for cls in a.classes.split(","):
        d = os.path.join(a.out, "calib", "grid_full")
        if os.path.isdir(os.path.join(d, cls)):
            grids[cls] = AG.Grid(d, cls)
        sp = os.path.join(a.out, "k7", "selection_%s.json" % cls)
        sel[cls] = json.load(open(sp, encoding="utf-8")).get("c_full") if os.path.isfile(sp) else None
    q5 = json.load(open(os.path.join(a.out, "calib", "q5_full.json"), encoding="utf-8")) if os.path.isfile(os.path.join(a.out, "calib", "q5_full.json")) else {}
    # k для abs-варианта: из эталона подсказок (pairs_all.csv)
    kref = {}
    refp = os.path.join(G.HOME, "audit3", "hints", "crypto", "pairs_all.csv")
    if os.path.isfile(refp):
        for r in csv.DictReader(open(refp, encoding="utf-8")):
            kref[(r["ex1"], r["sym1"], r["ex2"], r["sym2"])] = int(float(r["k"] or 0))
    out_rows, w2_rows, summary = [], [], []
    for name, e1, s1, e2, s2 in HINTS:
        nk = norm_key(e1, s1, e2, s2)
        p = byid.get((nk[0][0], nk[0][1], nk[1][0], nk[1][1]))
        v = verd.get(nk, ("не в asset_pairs", "", ""))
        rec = dict(name=name, ex1=e1, sym1=s1, ex2=e2, sym2=s2, verdict=v[0], c1d2=v[1], in_universe=p is not None, market=p["market"] if p else None,
                   cls=p["cls"] if p else None, cell=p["cell"] if p else None, pid=p["pid"] if p else None)
        if p is not None and p["cls"] in grids and world.in_main_series(p):
            gr = grids[p["cls"]]
            row = gr.pids.index(p["pid"]) if p["pid"] in gr.pids else None
            rec["in_main_series"] = row is not None
            if row is not None:
                qq = q5.get(p["pid"])
                rec["hl15_hi"] = qq
                rec["q5_T120"] = bool(qq is not None and qq <= 120 / 4.0)
                rec["q5_T360"] = bool(qq is not None and qq <= 360 / 4.0)
                rec["q5_T1440"] = bool(qq is not None and qq <= 1440 / 4.0)
                if sel.get(p["cls"]):
                    ci = gr.cindex[tuple(sel[p["cls"]])]
                    rec["at_selected"] = dict(combo=sel[p["cls"]], **per_trade(gr, ci, row))
                for ci, c in enumerate(gr.combos):
                    r = per_trade(gr, ci, row)
                    out_rows.append(dict(name=name, pid=p["pid"], theta=c[0], k=c[1], p=c[2], T=c[3], s_stop=c[4], trades=r["trades"], gross_trade=r["gross"], fund_trade=r["fund"],
                                         net_trade=r["net"], share_le2=r["le2"]))
                rec["ref_points"] = []
                for rp in REF_POINTS:
                    ci = gr.cindex.get(rp)
                    if ci is not None:
                        rec["ref_points"].append(dict(combo=list(rp), **per_trade(gr, ci, row)))
                rec["grid_trades_total_all_combos"] = int(sum(per_trade(gr, ci, row)["trades"] for ci in range(len(gr.combos))))
        else:
            rec["in_main_series"] = False
        # W2: правило «как в видео» на панелях формирования (отдельно от универсума; без маски сессии, как в отчёте подсказок)
        fa = os.path.join(G.HOME, "panels", e1, W.safe_name(s1) + ".sap")
        fb = os.path.join(G.HOME, "panels", e2, W.safe_name(s2) + ".sap")
        if os.path.isfile(fa) and os.path.isfile(fb):
            oA, cA = G.load_panel_range(fa, G.D_FORM0, a.last_day, what="hslice")
            oB, cB = G.load_panel_range(fb, G.D_FORM0, a.last_day, what="hslice")
            k = kref.get((e1, s1, e2, s2), 0)
            tab = HI.pair_table(oA, cA, oB, cB, k)
            for row in tab:
                n = row["n"]
                n1 = row["n_g1"]
                w2_rows.append(dict(name=name, var=row["var"], theta=row["theta"], n_ep=row["n_ep"], n_ep_1min=row["n_d1"], n_ep_2min=row["n_d2"], n_ep_3p=row["n_d3p"], trades=n,
                                    gross0_mean=(row["sum_g0"] / n) if n else None, trades_p1=n1, gross_p1_mean=(row["sum_g1"] / n1) if n1 else None,
                                    net_taker_p1_mean=(row["sum_g1"] / n1 - HI.FEE_T) if n1 else None, net_maker_p1_mean=(row["sum_g1"] / n1 - HI.FEE_M) if n1 else None))
            # базовые описательные величины пары
            s, valid = HI.L.spread_series(cA, cB)
            rec["valid_minutes"] = int(valid.sum())
            rec["valid_share"] = float(valid.mean())
            b, sig, _ = HI.L.basis_sigma(s, 1440, 5, 240, None)
            dev = s - b
            fin = np.isfinite(dev)
            rec["dev_abs_max_bps"] = float(np.nanmax(np.abs(dev[fin]))) if fin.any() else None
            rec["dev_abs_p99_bps"] = float(np.quantile(np.abs(dev[fin]), 0.99)) if fin.any() else None
            rec["sigma_rob_median_bps"] = float(np.nanmedian(sig[np.isfinite(sig)])) if np.isfinite(sig).any() else None
        summary.append(rec)
        print(name, "| универсум:", rec["in_universe"], "| рынок:", rec["market"], "| основной ряд:", rec.get("in_main_series"), "| вердикт:", rec["verdict"])
    os.makedirs(os.path.join(a.out, "k7"), exist_ok=True)
    with open(os.path.join(a.out, "k7", "hints_c2_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True, default=lambda o: None if (isinstance(o, float) and not math.isfinite(o)) else (float(o) if isinstance(o, (np.floating, np.integer)) else str(o)))
    for fn, rows in (("hints_c2_grid.csv", out_rows), ("hints_w2_formation.csv", w2_rows)):
        if rows:
            with open(os.path.join(a.out, "k7", fn), "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator=chr(10))
                w.writeheader()
                for r in rows:
                    w.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
