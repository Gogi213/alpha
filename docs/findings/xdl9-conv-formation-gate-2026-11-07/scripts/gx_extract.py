#!/usr/bin/env python3
"""TK-493, задача C (не код репозитория): выжимка чисел из выходов калибровки набора (out каталог conv_run/conv_k7) -> summary_<name>.json.

Только чтение выходов формирования (сетки уже посчитаны); панели не читаются; окно не читается.
  python gx_extract.py --out DIR --name NAME --dest DEST
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np

CODE = os.path.expanduser("~/xdl9/c2g/repo/tools/compute/statarb/xdl9/conv")
sys.path.insert(0, CODE)
import conv_agg as AG       # noqa: E402
import conv_grid as GR      # noqa: E402

FI = GR.FI
CODES = ["CV", "S0", "S1", "S2", "S3", "SES", "TO", "END"]


def jl(p):
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def per_rows(gr, ci, rows):
    rows = np.asarray(rows, int)
    if len(rows) == 0:
        return dict(trades=0, pairs=0)
    tn = gr.tab[ci][rows]
    n = float(tn[:, :, FI["n"]].sum())
    ser = AG.y_series(gr, ci, rows)["primary"]
    iv = AG.interval(ser) if ser else None
    out = dict(trades=int(n), pairs=int(len(rows)), assets=AG.n_assets_with_trades(gr, ci, rows), days=AG.n_days_with_trades(gr, ci, rows),
               gross=(float(tn[:, :, FI["gross"]].sum()) / n) if n else None, net=(float(tn[:, :, FI["net"]].sum()) / n) if n else None,
               fund=(float(tn[:, :, FI["fund"]].sum()) / n) if n else None, le2=(float(tn[:, :, FI["n_le2"]].sum()) / n) if n else None)
    if iv:
        out.update(mean_yd=iv["mean"], lo=iv.get("lo"), hi=iv.get("hi"), n_days=iv["n"], n_eff=iv["n_eff"], df=iv["df"])
    else:
        out.update(mean_yd=None, lo=None, hi=None)
    return out


def ex_of(pid):
    a, rest = pid.split("~", 1)
    return a, rest.split("|")[0]


def exit_table(path):
    if not os.path.isfile(path):
        return None
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    res = {}
    for r in rows:
        c = CODES[int(r["code"])]
        d = res.setdefault(c, dict(n=0, net=0.0, gross=0.0))
        d["n"] += 1
        d["net"] += float(r["net"])
        d["gross"] += float(r["gross"])
    return dict(total=len(rows), by_code={k: dict(n=v["n"], net_mean=v["net"] / v["n"], gross_mean=v["gross"] / v["n"]) for k, v in res.items()},
                deferred=sum(int(r["deferred"]) for r in rows), ffill=sum(int(r["ffill"]) for r in rows), mtm=sum(int(r["mtm"]) for r in rows),
                wins=sum(1 for r in rows if float(r["net"]) > 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--classes", default="crypto,eq")
    a = ap.parse_args()
    O = a.out
    res = dict(name=a.name, out=O)
    res["k2"] = {nm: jl(os.path.join(O, nm + ".json")) for nm in ("k2_calib", "k2_train")}
    res["summary"] = jl(os.path.join(O, "k7", "summary.json"))
    res["universe"] = jl(os.path.join(O, "k7", "universe.json"))
    res["classes"] = {}
    for cls in a.classes.split(","):
        d = os.path.join(O, "calib", "grid_full")
        if not os.path.isdir(os.path.join(d, cls)):
            continue
        gr = AG.Grid(d, cls)
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls)) or {}
        c = dict(pairs=len(gr.pids), assets=len(set(gr.keys)), cells=len(set(gr.cells)), exch=sorted({e for ex in gr.exch for e in ex}))
        c["pairs_gate"] = sum(1 for pid in gr.pids if "gate" in ex_of(pid))
        c["assets_gate"] = len({k for pid, k in zip(gr.pids, gr.keys) if "gate" in ex_of(pid)})
        c["cells_gate"] = len({cl for pid, cl in zip(gr.pids, gr.cells) if "gate" in ex_of(pid)})
        c["classpar_full"] = (jl(os.path.join(O, "calib", "classpar_full.json")) or {}).get(cls)
        c["structural_full"] = (jl(os.path.join(O, "calib", "structural_full.json")) or {}).get(cls)
        c["classpar_train"] = (jl(os.path.join(O, "train", "classpar_train.json")) or {}).get(cls)
        keep = ("formed", "c_full", "c_train", "plateau", "agree", "a3b", "a3b_detail", "a8", "reasons", "distinct_behaviours", "k8_selected_mean_yd", "k8_reversed_mean_yd",
                "p_win", "grid_positive_mean_share", "counters_selected", "tail_selected", "katok_bounds", "cells_admitted", "roll_fallback_a")
        c["sel"] = {k: sel.get(k) for k in keep}
        tab = sel.get("table") or []
        c["grid_rows"] = len(tab)
        c["grid_ok_rows"] = sum(1 for r in tab if r.get("ok"))
        c["grid_pos_mean"] = sum(1 for r in tab if r.get("mean") is not None and r["mean"] > 0)
        c["grid_pos_mean_p3"] = sum(1 for r in tab if r.get("mean") is not None and r["mean"] > 0 and r["p"] == 3)
        c["grid_p3_rows"] = sum(1 for r in tab if r["p"] == 3)
        c["grid_pos_lo"] = sum(1 for r in tab if r.get("lo") is not None and r["lo"] > 0)
        c["grid_pos_lo_ok"] = sum(1 for r in tab if r.get("ok") and r.get("lo") is not None and r["lo"] > 0)
        srt = sorted([r for r in tab if r.get("ok") and r.get("lo") is not None], key=lambda r: -r["lo"])
        c["top5_lcb"] = [{k: r.get(k) for k in ("theta", "k", "p", "T", "s_stop", "trades", "assets", "days", "mean", "lo", "hi", "mean_trade", "gross_trade", "n_eff")} for r in srt[:5]]
        cf = sel.get("c_full")
        if cf:
            ci = gr.cindex[tuple(cf)]
            allr = np.arange(len(gr.pids))
            row = [r for r in tab if r["ci"] == ci][0]
            c["at_cfull"] = {k: row.get(k) for k in ("theta", "k", "p", "T", "s_stop", "trades", "assets", "days", "mean", "lo", "hi", "n_days", "n_eff", "df", "block", "mean_trade", "maker_mean_trade",
                                                    "share_le2", "gross_trade", "fund_trade", "share_def", "share_ff", "win_rate", "win_loss_ratio")}
            c["at_cfull"]["lo_pos"] = bool(row.get("lo") is not None and row["lo"] > 0)
            gate_rows = [i for i, pid in enumerate(gr.pids) if "gate" in ex_of(pid)]
            non_rows = [i for i, pid in enumerate(gr.pids) if "gate" not in ex_of(pid)]
            c["cfull_gate_pairs"] = per_rows(gr, ci, gate_rows)
            c["cfull_nongate_pairs"] = per_rows(gr, ci, non_rows)
            c["cfull_H0"] = per_rows(gr, ci, gr.rows(asset_half=0))
            c["cfull_H1"] = per_rows(gr, ci, gr.rows(asset_half=1))
            c["cfull_by_cell"] = {}
            for cell in sorted(set(gr.cells)):
                rr = gr.rows(cells=[cell])
                pr = per_rows(gr, ci, rr)
                c["cfull_by_cell"][cell] = {k: pr.get(k) for k in ("pairs", "trades", "assets", "days", "net", "mean_yd", "lo")}
            ex_all = {}
            for e in c["exch"]:
                rr = np.array([i for i, pid in enumerate(gr.pids) if e in ex_of(pid)], int)
                pr = per_rows(gr, ci, rr)
                ex_all[e] = {k: pr.get(k) for k in ("pairs", "trades", "assets", "net", "mean_yd", "lo")}
            c["cfull_by_exch_involved"] = ex_all
            c["exits"] = exit_table(os.path.join(O, "calib", "grid_k8_%s" % cls, cls, "trades.csv"))
            c["k8_shift"] = jl(os.path.join(O, "k7", "k8_shift_%s.json" % cls))
        # допуск клеток
        cr = []
        p = os.path.join(O, "k7", "cells_%s.csv" % cls)
        if os.path.isfile(p):
            cr = list(csv.DictReader(open(p, encoding="utf-8")))
        c["cells_n"] = len(cr)
        c["cells_pass_A"] = [sum(1 for r in cr if r["A%d" % i] == "1") for i in range(1, 8)]
        c["cells_admitted_n"] = sum(1 for r in cr if r["tradable"] == "1")
        res["classes"][cls] = c
    os.makedirs(a.dest, exist_ok=True)

    def fix(o):
        if isinstance(o, (np.floating, float)):
            return None if not math.isfinite(float(o)) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.bool_):
            return bool(o)
        return str(o)
    path = os.path.join(a.dest, "summary_%s.json" % a.name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, sort_keys=True, default=fix)
    print("summary:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
