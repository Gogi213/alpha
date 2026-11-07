#!/usr/bin/env python3
"""TK-493, П-38 r2 §3.2, §7.1 K1b: сколько пар вне универсума C2 (c1d2 = 0) имеют эпизоды с отклонением >= 160 bps от базиса на формировании, по вердиктам R2' и причине исключения.

Описательно: универсум от этого не меняется. Берутся строки asset_pairs.csv: scope «между биржами», крипта-крипта, c1d2 = 0, обе панели существуют; отклонение dev = s - b (базис - медиана за 1440 минут,
пересчёт кратно 5, >= 240 наблюдений - тот же conv_lib.basis_sigma, что в основном счёте); эпизод - серия подряд идущих минут |dev| >= theta одного знака. Формирование читается через conv_guard
(K0 обязателен); окно не читается.
  python conv_k1b.py --out OUT --last-day 2478 --theta 160 --threads 4
"""
import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_hints as HI     # noqa: E402
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402

_CTX = {}


def _task(r):
    pa = os.path.join(G.HOME, "panels", r["ex1"], W.safe_name(r["sym1"]) + ".sap")
    pb = os.path.join(G.HOME, "panels", r["ex2"], W.safe_name(r["sym2"]) + ".sap")
    if not (os.path.isfile(pa) and os.path.isfile(pb)):
        return None
    d1 = _CTX["last_day"]
    _, cA = G.load_panel_range(pa, G.D_FORM0, d1, what="k1b")
    _, cB = G.load_panel_range(pb, G.D_FORM0, d1, what="k1b")
    s, valid = L.spread_series(cA, cB)
    if valid.sum() < 300:
        return (r["verdict"], r["why"], 0, 0, int(valid.sum()))
    b, _, _ = L.basis_sigma(s, 1440, 5, 240, None)
    dev = s - b
    res = []
    for th in _CTX["thetas"]:
        st, dur = HI.run_lengths(dev, th)
        res.append((len(dur), int((dur > 2).sum())))
    return (r["verdict"], r["why"], res, 1, int(valid.sum()))


def _init(last_day, thetas, params, script):
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    _CTX.update(last_day=last_day, thetas=thetas)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--last-day", type=int, default=2478)
    ap.add_argument("--theta", type=float, nargs="+", default=[160.0, 320.0])
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    script = os.path.join(HERE, "conv_run.py")
    params = (os.path.join(HERE, "P-38-params-2026-11-07.csv"), None)
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    G.check_days(a.last_day, what="k1b")
    G.require_k0()
    G.ensure_params_committed()
    rows = [r for r in csv.DictReader(open(os.path.join(G.HOME, "asset_pairs.csv"), encoding="utf-8"))
            if r.get("c1d2") == "0" and r.get("scope") == "между биржами" and r["big1"] == "крипта" and r["big2"] == "крипта"]
    if a.limit:
        rows = rows[:a.limit]
    ctx = mp.get_context("fork")
    with ctx.Pool(a.threads, initializer=_init, initargs=(a.last_day, a.theta, params, script)) as pool:
        res = list(pool.imap(_task, rows, chunksize=20))
    agg = defaultdict(lambda: defaultdict(float))
    for r in res:
        if r is None:
            continue
        v, why, per, flag, nv = r
        key = (v, why)
        agg[key]["pairs_with_panels"] += 1
        if flag:
            agg[key]["pairs_evaluated"] += 1
            for j, th in enumerate(a.theta):
                ne, nl = per[j]
                agg[key]["pairs_with_ep_%g" % th] += int(ne > 0)
                agg[key]["episodes_%g" % th] += ne
                agg[key]["pairs_with_ep_gt2min_%g" % th] += int(nl > 0)
    out = [dict(verdict=k[0], why=k[1], **dict(v)) for k, v in sorted(agg.items())]
    os.makedirs(os.path.join(a.out, "k7"), exist_ok=True)
    json.dump(dict(rows_total=len(rows), thetas=a.theta, by_verdict_why=out), open(os.path.join(a.out, "k7", "k1b.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    tot = defaultdict(float)
    for o in out:
        for k, v in o.items():
            if isinstance(v, float):
                tot[k] += v
    print("K1b: строк c1d2 = 0 (крипта, между биржами) %d; итого %s" % (len(rows), dict(tot)))
    for o in out:
        print(o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
