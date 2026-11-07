#!/usr/bin/env python3
"""TK-493, П-39 §5.4, §11.1 п. 10: измеряемые пороги ПОИСКА по ФОРМИРОВАНИЮ (сутки 2472..2478, читаются только они) -> addendum `P-39-params-calib-<дата>.csv` (коммит ДО поиска).

  β(n, q), n ∈ {1, 3, 7}, q ∈ {0,90; 0,95; 0,99}: q-квантиль S_n = |медиана s за n суток до t| по парам-суткам крипты; значение берётся на 00:00 UTC суток d ∈ 2472..2478
        (на 2472 даже n = 7 имеет полную историю с 2465); нужно >= 240 валидных наблюдений; метод квантиля - np.quantile(linear). Выбор «значение на 00:00» - прочтение Инженера
        (протокол говорит «по парам-суткам»): помечено в addendum.
  γ(q), q ∈ {0,95; 0,99}: q-квантиль max(|r_A|, |r_B|), r_leg = 1e4·ln(close_t / последний известный close на t-60), по парам-минутам крипты (обе ноги валидны) на сутках 2472..2478.
Читает панели только через conv_xguard (защита поиска); сутки > 2478 не читает (проверка в коде). Пишет под ~/xdl9/c2x с баннером.
  python explore_thresholds.py --out ~/xdl9/c2x [--threads 7] [--limit-pairs N]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_lib as L            # noqa: E402
import conv_explore as EX       # noqa: E402

DAYS = list(range(2472, 2479))
NS = (1, 3, 7)
QB = (0.90, 0.95, 0.99)
QG = (0.95, 0.99)
CTX = {}


def pair_task(p):
    world = CTX["world"]
    oA, cA, oB, cB, _ = world.load_pair_raw(p, EX.D0, EX.H1_LAST)
    s, valid = L.spread_series(cA, cB)
    sn = {n: [EX.sn_at(s, (d - EX.D0) * EX.MIN_DAY, n * EX.MIN_DAY) for d in DAYS] for n in NS}
    snu = {n: [EX.sn_at(s, (d - EX.D0) * EX.MIN_DAY, n * EX.MIN_DAY, unit_fix=True) for d in DAYS] for n in NS}
    lcA = np.where(L.ffill_idx(np.isfinite(cA)) >= 0, cA[np.maximum(L.ffill_idx(np.isfinite(cA)), 0)], np.nan)
    lcB = np.where(L.ffill_idx(np.isfinite(cB)) >= 0, cB[np.maximum(L.ffill_idx(np.isfinite(cB)), 0)], np.nan)
    rA, rB = EX.r60_bps(cA, lcA), EX.r60_bps(cB, lcB)
    rmax = np.fmax(np.abs(rA), np.abs(rB))
    lo = (DAYS[0] - EX.D0) * EX.MIN_DAY
    sel = valid[lo:] & np.isfinite(rmax[lo:])
    return p["pid"], sn, snu, rmax[lo:][sel].astype(np.float32)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/xdl9"))
    ap.add_argument("--out", default=XG.c2x_home())
    ap.add_argument("--threads", type=int, default=7)
    ap.add_argument("--limit-pairs", type=int, default=0)
    a = ap.parse_args(argv)
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    XG.log_event("thresholds_start", days=[DAYS[0], DAYS[-1]])
    t0 = time.time()
    world = EX.make_world(a.root, "main", os.path.join(a.out, "turn"))
    pairs = EX.select_pairs(world, "main", a.limit_pairs)
    CTX["world"] = world
    sn_vals = {n: [] for n in NS}
    snu_vals = {n: [] for n in NS}
    rmax_all = []
    for pid, sn, snu, rm in EX.pool_map(pair_task, pairs, a.threads):
        for n in NS:
            sn_vals[n] += [x for x in sn[n] if np.isfinite(x)]
            snu_vals[n] += [x for x in snu[n] if np.isfinite(x)]
        rmax_all.append(rm)
    rmax_all = np.concatenate(rmax_all)
    beta = {}
    for n in NS:
        arr = np.array(sn_vals[n])
        for q in QB:
            beta["%d|%.2f" % (n, q)] = float(np.quantile(arr, q))
    beta_u = {}
    for n in NS:
        arr = np.array(snu_vals[n])
        for q in QB:
            beta_u["%d|%.2f" % (n, q)] = float(np.quantile(arr, q))
    n_unit = {str(n): int(sum(1 for a_, b_ in zip(sn_vals[n], snu_vals[n]) if abs(a_ - b_) > 1000.0)) for n in NS}
    gamma = {"%.2f" % q: float(np.quantile(rmax_all, q)) for q in QG}
    info = dict(pairs=len(pairs), days=DAYS, n_pairdays={str(n): len(sn_vals[n]) for n in NS}, n_pairminutes=int(len(rmax_all)), quantile_method="numpy linear",
                beta=beta, beta_unit=beta_u, n_pairdays_unit_shift=n_unit, gamma=gamma, seconds=round(time.time() - t0, 1), max_day_read=int(XG.STATE.max_read.value))
    assert info["max_day_read"] <= EX.H1_LAST, "пороги читают только формирование"
    XG.write_json(os.path.join(a.out, "out", "thresholds", "thresholds.json"), info)
    rows = [
        ["calib_beta_sgap", json.dumps(beta, sort_keys=True), "измерено (формирование 2472..2478)", "P-39 r1 07.11", "explore_thresholds.py: q-квантиль S_n по парам-суткам крипты, значение на 00:00 UTC (прочтение Инженера), >=240 валидных"],
        ["calib_beta_sgap_unit", json.dumps(beta_u, sort_keys=True), "измерено (формирование 2472..2478), семейство Cu (добавлено сверх протокола)", "P-39 r1 07.11", "explore_thresholds.py: то же с снятием сдвига единиц, кратного ln(10)·1e4 bps (пары x10^k)"],
        ["calib_gamma_outlier", json.dumps(gamma, sort_keys=True), "измерено (формирование 2472..2478)", "P-39 r1 07.11", "explore_thresholds.py: q-квантиль max(|r_A|,|r_B|) за 60 мин (bps) по парам-минутам крипты с обеими валидными ногами"],
        ["h_q90_B1", "121", "измерено (35 суток, prep_facts_output.txt)", "P-39 r1 07.11", "docs/research/P-39-prep-2026-11-07/prep_facts_output.txt"],
        ["h_q90_B2", "69", "измерено (35 суток, prep_facts_output.txt)", "P-39 r1 07.11", "docs/research/P-39-prep-2026-11-07/prep_facts_output.txt"],
        ["calib_n_pairdays", json.dumps(info["n_pairdays"], sort_keys=True), "измерено", "P-39 r1 07.11", "число пар-суток под β"],
        ["calib_n_pairminutes", str(info["n_pairminutes"]), "измерено", "P-39 r1 07.11", "число пар-минут под γ"],
    ]
    XG.write_csv(os.path.join(a.out, "out", "thresholds", "P-39-params-calib.csv"), ["key", "value", "status", "when", "source"], rows)
    XG.log_max_read("thresholds_end")
    print(json.dumps(info, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
