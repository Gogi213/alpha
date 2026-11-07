#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.1 K7-K9: оркестратор выбора на уровне класса, условий формирования, допуска клеток и описательных таблиц калибровки.

Вход - каталоги сетки conv_run grid (по конвейерам):
  <out>/calib/grid_full        Pi_full: все пары, 180 комбинаций, числа класса по Pi_full (сутки 2465..2478)
  <out>/train/grid_train       Pi_train: сутки 2465..2471, числа класса только по ним
  <out>/calib/grid_H0, grid_H1 половины активов (A3b): числа класса - только по своей половине
  <out>/calib/grid_tv_<cls>    c_обуч с числами Pi_train на сутках 2466..2478 (проверка A3 - сутки 2472..2478)
  <out>/calib/grid_h1on0_<cls> c_H0 с числами H0 на парах H1;  grid_h0on1_<cls> - наоборот (A3b)
Подкоманды:
  halves   pairs_H0.txt / pairs_H1.txt (pid основного ряда; половина = чётность первого байта sha256 ключа актива)
  pick     выбор комбинаций c_полн / c_обуч / c_H0 / c_H1 по классам; файлы combo_*.json для следующих прогонов -> <out>/calib/pick.json
  final    условия формирования класса, допуск клеток A1-A7, K1c, K8 (а, в), таблицы -> <out>/k7/
Выбор и допуск - функции conv_agg (правила протокола); здесь только оркестровка и печать. Ничего не подбирается по результату.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_agg as AG       # noqa: E402
import conv_ctrl as CT      # noqa: E402
import conv_grid as GR      # noqa: E402
import conv_guard as G      # noqa: E402
import conv_world as W      # noqa: E402

FI = GR.FI


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def fix(o):
        if isinstance(o, (np.floating,)):
            return None if not math.isfinite(float(o)) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return str(o)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, default=fix)


def gpath(out, mode, tag):
    return os.path.join(out, mode, "grid_" + tag)


def load_grid(out, mode, tag, cls):
    d = gpath(out, mode, tag)
    if not os.path.isdir(os.path.join(d, cls)):
        return None
    return AG.Grid(d, cls)


def half_of(key):
    return hashlib.sha256(key.encode("utf-8")).digest()[0] % 2


# ------------------------------------------------------------------ halves
def cmd_halves(a):
    world = W.World(G.HOME)
    pairs = [p for p in world.universe() if world.in_main_series(p)]
    for h in (0, 1):
        ps = [p["pid"] for p in pairs if half_of(p["key"]) == h]
        with open(os.path.join(a.out, "pairs_H%d.txt" % h), "w", encoding="utf-8", newline=chr(10)) as f:
            f.write(chr(10).join(ps) + chr(10))
        print("H%d: пар %d, активов %d" % (h, len(ps), len({p["key"] for p in pairs if half_of(p["key"]) == h})))
    return 0


# ------------------------------------------------------------------ pick
def combo_json(gr, ci):
    return None if ci is None else [list(gr.combos[ci])]


def cmd_pick(a):
    res = {}
    for cls in a.classes.split(","):
        gf = load_grid(a.out, "calib", "full", cls)
        gt = load_grid(a.out, "train", "train", cls)
        h0 = load_grid(a.out, "calib", "H0", cls)
        h1 = load_grid(a.out, "calib", "H1", cls)
        r = {}
        for name, gr, dn in (("full", gf, None), ("train", gt, 6), ("H0", h0, None), ("H1", h1, None)):
            ci = AG.class_selection(gr, days_n=dn)[0] if gr is not None else None
            r[name] = None if ci is None else list(gr.combos[ci])
            cf = os.path.join(a.out, "calib", "combo_%s_%s.json" % (name, cls))
            if ci is not None:
                jdump(cf, combo_json(gr, ci))
            elif os.path.isfile(cf):
                os.remove(cf)
        res[cls] = r
        print(cls, r)
    jdump(os.path.join(a.out, "calib", "pick.json"), res)
    return 0


# ------------------------------------------------------------------ final
def mean_of(ser):
    return (sum(ser) / len(ser)) if ser else None


def a3b_from_grids(out, cls, pick):
    """A3b: c_H0 (выбрана на H0 с числами H0) должна дать среднее y_d > 0 на парах H1 при тех же числах H0; затем наоборот."""
    ok, det = [], {}
    for h, on in ((0, "h1on0"), (1, "h0on1")):
        gx = load_grid(out, "calib", "%s_%s" % (on, cls), cls)
        c = pick.get("H%d" % h)
        if gx is None or c is None:
            ok.append(False)
            det["H%d" % h] = dict(combo=c, mean=None, note="нет комбинации или нет прогона")
            continue
        ser = AG.y_series(gx, 0, np.arange(len(gx.pids)))["primary"]
        m = mean_of(ser)
        trades = AG.n_trades(gx, 0, np.arange(len(gx.pids)))
        ok.append(bool(m is not None and m > 0))
        det["H%d" % h] = dict(combo=c, mean=m, trades=trades, days=len(ser) if ser else 0)
    return dict(ok=ok, detail=det)


def cell_table(gr, table_ci_rows, cells, first_cols):
    """Строки «ячейка x комбинация»: сделки, активы, сутки, средняя валовая и чистая на сделку, доля <= 2 мин, средний y_d."""
    rows = []
    for cell in cells:
        rr = gr.rows(cells=[cell])
        for ci, c in enumerate(gr.combos):
            tn = gr.tab[ci][rr]
            n = float(tn[:, :, FI["n"]].sum())
            ser = AG.y_series(gr, ci, rr)["primary"]
            rows.append(dict(cell=cell, pairs=int(len(rr)), theta=c[0], k=c[1], p=c[2], T=c[3], s_stop=c[4], trades=int(n),
                             assets=AG.n_assets_with_trades(gr, ci, rr), days=AG.n_days_with_trades(gr, ci, rr),
                             gross_trade=(float(tn[:, :, FI["gross"]].sum()) / n) if n else None,
                             fund_trade=(float(tn[:, :, FI["fund"]].sum()) / n) if n else None,
                             net_trade=(float(tn[:, :, FI["net"]].sum()) / n) if n else None,
                             share_le2=(float(tn[:, :, FI["n_le2"]].sum()) / n) if n else None,
                             mean_yd=mean_of(ser), n_days_y=len(ser) if ser else 0))
    return rows


def write_csv(path, rows, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator=chr(10))
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.10g" % r.get(c)) if isinstance(r.get(c), float) else r.get(c) for c in cols])


def eff_rank(series_list):
    """M_эфф = (Σλ)^2 / Σλ^2 по собственным числам матрицы корреляций рядов (по строкам - ряды); 1 ряд -> 1; константный ряд исключается."""
    ok = [np.asarray(s, float) for s in series_list if len(s) >= 3 and np.std(s) > 0]
    if len(ok) == 0:
        return 0.0
    if len(ok) == 1:
        return 1.0
    n = min(len(s) for s in ok)
    C = np.corrcoef(np.stack([s[:n] for s in ok]))
    lam = np.clip(np.linalg.eigvalsh(C), 0, None)
    return float(lam.sum() ** 2 / (lam ** 2).sum())


def cmd_final(a):
    out = a.out
    k7 = os.path.join(out, "k7")
    pick_all = json.load(open(os.path.join(out, "calib", "pick.json"), encoding="utf-8"))
    summary = {}
    admitted_series = []
    for cls in a.classes.split(","):
        gf = load_grid(out, "calib", "full", cls)
        gt = load_grid(out, "train", "train", cls)
        pick = pick_all[cls]
        gtv = load_grid(out, "calib", "tv_" + cls, cls)
        a3b = a3b_from_grids(out, cls, pick)
        res = AG.class_formation(gf, gt, cls, a3b_given=a3b)
        allrows = np.arange(len(gf.pids))
        # таблица сетки класса (полная): + валовая, фандинг, доля отложенных входов, выходов по последней цене
        for r in res["table"]:
            ci = r["ci"]
            tn = gf.tab[ci][allrows]
            n = float(tn[:, :, FI["n"]].sum())
            r["gross_trade"] = float(tn[:, :, FI["gross"]].sum()) / n if n else None
            r["fund_trade"] = float(tn[:, :, FI["fund"]].sum()) / n if n else None
            r["share_def"] = float(tn[:, :, FI["n_def"]].sum()) / n if n else None
            r["share_ff"] = float(tn[:, :, FI["n_ff"]].sum()) / n if n else None
            tm = AG.tail_metrics(gf, ci, allrows)
            r["win_rate"] = tm["win_rate"] if tm else None
            r["win_loss_ratio"] = tm["win_loss_ratio"] if tm else None
        cols = ["theta", "k", "p", "T", "s_stop", "trades", "days", "assets", "n_days", "block", "n_eff", "df", "mean", "lo", "hi", "mean_trade", "gross_trade", "fund_trade",
                "maker_mean_trade", "share_le2", "share_def", "share_ff", "win_rate", "win_loss_ratio", "ok"]
        write_csv(os.path.join(k7, "grid_class_%s.csv" % cls), res["table"], cols)
        # K1c, tail bounds
        res["k1c"] = CT.k1c_power(gf)
        tms = [AG.tail_metrics(gf, ci, allrows) for ci in range(len(gf.combos))]
        res["katok_bounds"] = AG.katok_bounds(tms)
        # метаданные пар (Ролл)
        mdir = os.path.join(out, "calib", "cache", "L%d" % a.L_full, "meta")
        meta = AG._meta_by_pid(mdir, gf.pids)
        pos = [x for m in meta.values() for x in (m.get("roll_full") or []) if x is not None]
        fb = float(np.median(pos)) if pos else None
        res["roll_fallback_a"] = fb
        ci_full = gf.cindex[tuple(res["c_full"])] if res.get("c_full") else None
        cells = sorted(set(gf.cells))
        cells_out = []
        if ci_full is not None:
            for cell in cells:
                ad = AG.admit_cell(gf, gtv, cell, ci_full, 0 if gtv is not None else None, meta, fb)
                ad["tradable"] = bool(ad.get("admitted") and res["formed"])
                cells_out.append(ad)
            res["cells_admitted"] = [c["cell"] for c in cells_out if c["tradable"]]
            res["tail_selected"] = tms[ci_full]
            # K8 (а): перевёрнутое направление при тех же сделках
            rev = AG.y_series(gf, ci_full, allrows, rev=True)["primary"]
            res["k8_reversed_mean_yd"] = mean_of(rev)
            res["k8_selected_mean_yd"] = mean_of(AG.y_series(gf, ci_full, allrows)["primary"])
            res["counters_selected"] = gf.cnt[ci_full]
            if a.perm:
                res["p_win"] = AG.perm_pwin(gf, allrows, a.perm)
            # описательно: лучшая и худшая комбинация и реальная доля прибыльных комбинаций класса
            means = [r["mean"] for r in res["table"] if r.get("mean") is not None]
            res["grid_positive_mean_share"] = float(np.mean([m > 0 for m in means])) if means else None
        # таблица «ячейка x комбинация» целиком
        ct = cell_table(gf, None, cells, None)
        write_csv(os.path.join(k7, "grid_cells_%s.csv" % cls), ct,
                  ["cell", "pairs", "theta", "k", "p", "T", "s_stop", "trades", "assets", "days", "gross_trade", "fund_trade", "net_trade", "share_le2", "mean_yd", "n_days_y"])
        # лучшая точка сетки по ячейке (описательно): max средний чистый на сделку при >= 20 сделок; «нет безубыточной точки» = такой точки нет
        best_by_cell = {}
        for r in ct:
            if r["trades"] >= 20 and r["net_trade"] is not None:
                b = best_by_cell.get(r["cell"])
                if b is None or r["net_trade"] > b["net_trade"]:
                    best_by_cell[r["cell"]] = r
        res["cells_best_point"] = {c: dict(theta=b["theta"], k=b["k"], p=b["p"], T=b["T"], s_stop=b["s_stop"], trades=b["trades"], net_trade=b["net_trade"], mean_yd=b["mean_yd"])
                                   for c, b in best_by_cell.items()}
        res["cells_no_breakeven"] = [c for c in cells if c not in best_by_cell or best_by_cell[c]["net_trade"] <= 0]
        res["cells_no_20_trades_anywhere"] = [c for c in cells if c not in best_by_cell]
        # допуск клеток: таблица
        cols_c = ["cell", "pairs", "trades", "assets", "days", "mean", "lo_t", "lo_boot", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "admitted", "tradable", "share_net_stale", "a3_mean"]
        rows_c = []
        for c in cells_out:
            d = dict(c)
            for i in range(7):
                d["A%d" % (i + 1)] = int(c["A"][i])
            d["admitted"] = int(bool(c.get("admitted")))
            d["tradable"] = int(c["tradable"])
            rows_c.append(d)
        write_csv(os.path.join(k7, "cells_%s.csv" % cls), rows_c, cols_c)
        if res["formed"]:
            for cell in res["cells_admitted"]:
                admitted_series.append(("%s|%s" % (cls, cell), AG.y_series(gf, ci_full, gf.rows(cells=[cell]))["primary"]))
        res["cells"] = [{k: v for k, v in c.items() if k != "A"} | {"A": [bool(x) for x in c["A"]]} for c in cells_out]
        res.pop("table_full_print", None)
        jdump(os.path.join(k7, "selection_%s.json" % cls), res)
        summary[cls] = dict(formed=res["formed"], c_full=res.get("c_full"), c_train=res.get("c_train"), reasons=res["reasons"], plateau=res.get("plateau"), agree=res.get("agree"),
                            a3b=res.get("a3b"), a8=res.get("a8"), distinct=res.get("distinct_behaviours"), cells_admitted=res.get("cells_admitted", []),
                            n_cells=len(cells), k8_reversed=res.get("k8_reversed_mean_yd"), k8_selected=res.get("k8_selected_mean_yd"), p_win=res.get("p_win"))
        print("класс %s: сформирован=%s c_полн=%s c_обуч=%s причины=%s допущено клеток %d из %d" % (cls, res["formed"], res.get("c_full"), res.get("c_train"), res["reasons"],
                                                                                              len(res.get("cells_admitted", [])), len(cells)))
    M = sum(len(v["cells_admitted"]) for v in summary.values())
    meff = eff_rank([s for _, s in admitted_series]) if admitted_series else 0.0
    summary["_all"] = dict(M=M, M_eff_formation_estimate=meff, grid_combos_per_class=180, classes=len(a.classes.split(",")), M_grid=180 * len(a.classes.split(",")))
    jdump(os.path.join(k7, "summary.json"), summary)
    print("M =", M, "M_эфф (оценка по суточным y_d формирования) =", meff)
    return 0


# ------------------------------------------------------------------ universe (K1)
def cmd_universe(a):
    """K1: состав универсума (по классам и рынкам), покрытие, тик, семантика open по биржам (доля минут open[t] = close[t-1]), доля пар с определённым полом sigma."""
    script = os.path.join(HERE, "conv_run.py")
    params = (os.path.join(HERE, "P-38-params-2026-11-07.csv"), None)
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    G.check_days(a.last_day, what="k1")
    G.require_k0()
    G.ensure_params_committed()
    world = W.World(G.HOME)
    U = world.universe()
    res = {"pairs_by_class_market": {}, "main_by_class": {}}
    for p in U:
        k = "%s|%s" % (p["cls"], p["market"])
        d = res["pairs_by_class_market"].setdefault(k, dict(pairs=0, cells=set(), assets=set()))
        d["pairs"] += 1
        d["cells"].add(p["cell"])
        d["assets"].add(p["key"])
    for k, d in res["pairs_by_class_market"].items():
        d["cells"], d["assets"] = len(d["cells"]), len(d["assets"])
    main = [p for p in U if world.in_main_series(p)]
    seen = {}
    for p in main:
        for side in (0, 1):
            seen.setdefault(world.panel_path(p, side), p["legs"][side][0])
    ex_stat = {}
    for fp, ex in sorted(seen.items()):
        if not os.path.isfile(fp):
            continue
        o, c = G.load_panel_range(fp, G.D_FORM0, a.last_day, what="k1 legs")
        fin = np.isfinite(c)
        both = np.isfinite(o[1:]) & np.isfinite(c[:-1]) & np.isfinite(c[1:])
        ch = float(np.mean(o[1:][both] == c[:-1][both])) if both.any() else None
        d = ex_stat.setdefault(ex, dict(legs=0, valid_share=[], chain_share=[]))
        d["legs"] += 1
        d["valid_share"].append(float(fin.mean()))
        if ch is not None:
            d["chain_share"].append(ch)
    res["open_semantics_by_exchange"] = {ex: dict(legs=d["legs"], valid_share_median=float(np.median(d["valid_share"])), valid_share_ge50=float(np.mean(np.array(d["valid_share"]) >= 0.5)),
                                                  chain_share_median=float(np.median(d["chain_share"])) if d["chain_share"] else None) for ex, d in sorted(ex_stat.items())}
    mdir = os.path.join(a.out, "calib", "cache", "L%d" % a.L_full, "meta")
    cov = {}
    for p in main:
        fp = os.path.join(mdir, p["pid"] + ".json")
        if not os.path.isfile(fp):
            continue
        m = json.load(open(fp, encoding="utf-8"))
        d = cov.setdefault(p["cls"], dict(pairs=0, floor_defined=0, valid_share=[], mad0=[]))
        d["pairs"] += 1
        d["floor_defined"] += int(m.get("floor") is not None)
        d["valid_share"].append(m["n_valid"] / float((a.last_day - G.D_FORM0 + 1) * 1440))
        if m.get("mad0") is not None:
            d["mad0"].append(m["mad0"])
    res["main_by_class"] = {c: dict(pairs=d["pairs"], floor_defined=d["floor_defined"], valid_share_median=float(np.median(d["valid_share"])),
                                    valid_share_q10=float(np.quantile(d["valid_share"], 0.1)), mad0_share_mean=float(np.mean(d["mad0"])) if d["mad0"] else None) for c, d in cov.items()}
    jdump(os.path.join(a.out, "k7", "universe.json"), res)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str)[:3000])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["halves", "pick", "final", "universe"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--classes", default="crypto,eq")
    ap.add_argument("--perm", type=int, default=0)
    ap.add_argument("--L-full", type=int, default=1440, dest="L_full")
    ap.add_argument("--last-day", type=int, default=2478)
    a = ap.parse_args(argv)
    return dict(halves=cmd_halves, pick=cmd_pick, final=cmd_final, universe=cmd_universe)[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
