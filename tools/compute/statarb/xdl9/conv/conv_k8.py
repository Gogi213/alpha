#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.6 K8 (б): перенос минуты входа на ±5 и ±15 минут для выбранной комбинации класса c_полн (контроль специфичности сигнала по времени).

Берутся реальные закрытые сделки прогона `conv_run grid --combos <c_полн> --trades-out` (тег k8_<класс>); для каждой сделки вход переносится на delta минут: сигнал в t+delta, если там
выполнены пороговое условие и Q1/Q3/Q4/Q6/Q7 (conv_ctrl.eligible_minutes), направление - по знаку d_idio в сдвинутой минуте, длительность та же, отложенный вход по тем же правилам.
Результат - ряд y_d (Σ net сделок суток / активные пары-сутки реального прогона) и его среднее: «специфичность сигнала по времени» (реальное среднее должно быть выше сдвинутых).
Сравнение «реальный - сдвиг» считается на одном и том же наборе сутки x знаменатель. MTM-сделки не берутся (сравнение по закрытым).
  python conv_k8.py --out OUT --cls crypto --last-day 2478 --L 1440 --threads 4 --turn-dir TD
"""
import argparse
import csv
import json
import math
import multiprocessing as mp
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_agg as AG       # noqa: E402
import conv_ctrl as CT      # noqa: E402
import conv_grid as GR      # noqa: E402
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402
import conv_run as R        # noqa: E402
import conv_world as W      # noqa: E402

_CTX = {}
SHIFTS = (-15, -5, 5, 15)


def _init(root, odir, d0, d1, p0, Lw, cpd, combo, pairs_by_pid, turn_dir, params, script):
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    _CTX.update(world=W.World(root, turn_dir=turn_dir), odir=odir, d0=d0, d1=d1, p0=p0, L=Lw, cpd=cpd, combo=combo, pairs=pairs_by_pid)


def _task(item):
    pid, trades = item
    cx = _CTX
    p = cx["pairs"][pid]
    cfg = L.Cfg(L=cx["L"])
    cell_c = W.load_cell_c(cx["odir"], p["cell"], cfg.L)[0]
    ps, _ = W.load_series(cx["world"], p, cx["d0"], cx["d1"], cfg, cx["odir"], cell_c, want_turn=True, want_fund=True)
    cp = cx["cpd"][p["cls"]]
    period0 = (cx["p0"] - cx["d0"]) * 1440
    ft = L.Feats(ps, cp, cfg, period0)
    res = {}
    for dl in SHIFTS:
        days, done, skipped = CT.shifted_entries(ps, cp, cfg, cx["combo"], ft, trades, dl, period0)
        res[dl] = (days, done, skipped)
    return pid, res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cls", required=True)
    ap.add_argument("--last-day", type=int, default=2478)
    ap.add_argument("--L", type=int, default=1440)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--turn-dir", required=True)
    ap.add_argument("--tag", default=None)
    W.add_gate_args(ap)
    a = ap.parse_args(argv)
    W.apply_gate_args(a)
    script = os.path.join(HERE, "conv_run.py")
    params = (os.path.join(HERE, "P-38-params-2026-11-07.csv"), None)
    G.configure(go_window=False, script_path=script, params_paths=params, mode="calib", require_k0=True)
    G.check_days(a.last_day, what="k8")
    G.require_k0()
    G.ensure_params_committed()
    odir = os.path.join(a.out, "calib")
    tag = a.tag or ("k8_" + a.cls)
    gdir = os.path.join(odir, "grid_" + tag)
    combo = L.Combo(*json.load(open(os.path.join(gdir, "combos.json")))[0])
    cpd = R.class_params(os.path.join(odir, "classpar_full.json"))
    world = W.World(G.HOME, turn_dir=a.turn_dir)
    pairs = {p["pid"]: p for p in world.universe() if world.in_main_series(p) and p["cls"] == a.cls}
    trades = {}
    with open(os.path.join(gdir, a.cls, "trades.csv"), encoding="utf-8") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        for row in rd:
            pid = row[1]
            vals = [float(x) for x in row[3:]]
            trades.setdefault(pid, []).append(tuple(vals))
    # реальный прогон: суточные суммы закрытых сделок и знаменатель
    gr = AG.Grid(os.path.join(odir, "grid_" + tag), a.cls)
    nd = gr.nd
    first = gr.first_sig - gr.d0
    den = gr.act[0].sum(axis=0).astype(float)                      # [сутки]
    real = np.zeros(nd)
    for pid, trs in trades.items():
        for tr in trs:
            r = dict(zip(L.TRADE_FIELDS, tr))
            if r["mtm"]:
                continue
            real[(G.D_FORM0 * 1440 + int(r["f_out"])) // 1440 - gr.d0] += r["net"]
    days_ok = [j for j in range(first, nd) if den[j] > 0]

    def mean_y(num):
        return float(np.mean([num[j] / den[j] for j in days_ok])) if days_ok else None
    out = dict(cls=a.cls, combo=list(combo), days_with_active_pairs=len(days_ok), pairs_with_trades=len(trades), real_closed_trades=int(sum(1 for t in trades.values() for tr in t if not tr[L.TRADE_FIELDS.index("mtm")])),
               real_mean_yd_closed=mean_y(real), shifts={})
    items = sorted(trades.items())
    ctx = mp.get_context("fork")
    with ctx.Pool(a.threads, initializer=_init, initargs=(G.HOME, odir, G.D_FORM0, a.last_day, G.D_SIG0, a.L, cpd, combo, pairs, a.turn_dir, params, script)) as pool:
        parts = list(pool.imap(_task, items, chunksize=1))
    for dl in SHIFTS:
        num = np.zeros(nd)
        done = skipped = 0
        for pid, res in parts:
            days, d, s = res[dl]
            for dd, v in days.items():
                num[dd - gr.d0] += v
            done += d
            skipped += s
        out["shifts"][str(dl)] = dict(trades_done=done, skipped=skipped, mean_yd=mean_y(num))
    os.makedirs(os.path.join(a.out, "k7"), exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "k7", "k8_shift_%s.json" % a.cls), "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
