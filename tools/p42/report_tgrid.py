"""П-42 §13.9: итоги тиковой сетки шага A (задержка 4 мс) по вселенной → <ROOT>/report-tgrid.json и печать.

  P42_ROOT=data/p42v2-t544 python tools/p42/tickvars.py            (top3.json — правило §13.1 на тиковых рядах)
  P42_ROOT=data/p42v2-t544 P42_M='{"long":..,"short":..}' python tools/p42/report_tgrid.py
Кандидаты: база b0; перекрёстный (cB_ на сутках A, cA_ на сутках B — `phase.decide`, те же правила §9/§12); «по букве» (uB_/uA_), если есть; топ-3 (top3.json).
Испытания = 70 осей + расширения + совместные клетки + перекрёстный + «по букве» (как report.py). Холм — по P42_M (накопленный счётчик, §13.9).
"""
import json
import os
import sys

os.environ.setdefault("P42_FQ_COL", "first_day_ge_12M")
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

import count_pos  # noqa: E402
import detstat  # noqa: E402
import report_tick  # noqa: E402
from axes import AXES, BASE  # noqa: E402
from lib import A, B, ND, ROOT, SIDES, load_all, read_trades  # noqa: E402
from phase import decide, rng  # noqa: E402
from report import fq_days  # noqa: E402
from report_tick import halves_stats, verdict  # noqa: E402

detstat.patch(report_tick)
M = json.loads(os.environ["P42_M"]) if os.environ.get("P42_M") else None


def total_trades(side, P, D, va, vb):
    x = np.concatenate([D[side][va][A], D[side][vb][B]])
    nA = sum(1 for t in read_trades(P[(side, va)], side) if int(t["entry_day"]) < 30)
    nB = sum(1 for t in read_trades(P[(side, vb)], side) if int(t["entry_day"]) >= 30)
    return float(x.sum()), nA + nB


def grid_rows(side, D, P, T):
    """Все конфигурации сетки (stage1 + ext + joint): параметры, $/сут. A/B, сделки."""
    lines = {}
    for st in ("stage1", "ext", "joint"):
        f = os.path.join(ROOT, "variants-%s.txt" % st)
        if os.path.exists(f):
            for ln in open(f, encoding="utf-8"):
                ln = ln.strip()
                if ln:
                    vid, _, rest = ln.partition(" ")
                    lines[vid] = rest.replace("lat=4 latx=4", "").strip()
    rows = []
    for v in sorted(D[side]):
        if v not in lines:
            continue
        rows.append({"id": v, "params": lines[v], "A": float(D[side][v][A].mean()), "B": float(D[side][v][B].mean()),
                     "nA": T[side][v][0], "nB": T[side][v][1], "total": float(D[side][v].sum())})
    return rows


def main():
    r = rng()
    fq = fq_days()
    D, P, T = load_all()
    top = json.load(open(os.path.join(ROOT, "top3.json")))
    cfgf = json.load(open(os.path.join(ROOT, "cfg-final.json")))
    pe, pj = json.load(open(os.path.join(ROOT, "plan-ext.json"))), json.load(open(os.path.join(ROOT, "plan-joint.json")))
    cnt = count_pos.count(ROOT)
    out = {"root": ROOT, "sides": {}}
    for side in SIDES:
        n_stage1 = len([v for v in AXES for x in AXES[v] if x != BASE[v]])  # 70, как в report.py
        _, cfg, joint, _ = decide(D, side, r, T)
        have_cf = bool(cfg["A"] or cfg["B"]) and ("cA_" + side) in D[side] and ("cB_" + side) in D[side]
        have_l = ("uA_" + side) in D[side] and ("uB_" + side) in D[side]
        n_ext, n_joint = len(pe[side]), len(pj[side]["new_trials"])
        hon = json.load(open(os.path.join(ROOT, "cfg-honest.json")))[side]["cfg"] if os.path.isdir(os.path.join(ROOT, "honest")) else None  # §13.10: честный эталон
        have_h = hon is not None
        trials = n_stage1 + n_ext + n_joint + (1 if have_cf else 0) + (1 if have_l else 0) + (2 if have_h else 0)
        res = {"trials": trials, "n_stage1": n_stage1, "n_ext": n_ext, "n_joint": n_joint, "have_cf": have_cf, "have_honest": have_h, "cfg_honest": hon, "have_letter": have_l,
               "cfg": cfgf[side]["cfg"], "joint": cfgf[side]["joint"], "counts": {k: v for k, v in cnt[side].items() if not k.endswith("names")},
               "pos_names": cnt[side]["pos_names"], "pos10_names": cnt[side]["pos10_names"], "grid": grid_rows(side, D, P, T), "cand": {}}
        m = (M or {}).get(side, trials)
        z_h = ND.inv_cdf(1 - 0.05 / (2 * m))
        res["m"], res["z_holm"] = m, z_h
        cands = {"база": ("b0", "b0")}
        if have_cf:
            cands["перекрёстный"] = ("cB_" + side, "cA_" + side)
        if have_l and not (all(np.array_equal(D[side]["cA_" + side], D[side]["uA_" + side]) for _ in [0]) and all(np.array_equal(D[side]["cB_" + side], D[side]["uB_" + side]) for _ in [0])):
            cands["по букве"] = ("uB_" + side, "uA_" + side)
        for t in top[side]:
            cands["топ-3 " + t["vid"]] = (t["vid"], t["vid"])
        if have_h:  # hB на A, hA на B (как cB/cA); пустая конфигурация = база
            cands["честный эталон (§13.10)"] = (("hB_" + side) if hon["B"] else "b0", ("hA_" + side) if hon["A"] else "b0")
        if "v_s_1" in D[side]:  # слова владельца 15.11: «1 тоже попробуй» (стоп 1 %); уже в сетке как расширение края оси s — +0 испытаний (протокол §13.9, дополнение 15.11)
            cands["стоп 1 % (v_s_1)"] = ("v_s_1", "v_s_1")
        for name, (va, vb) in cands.items():
            if va not in D[side] or vb not in D[side]:
                continue
            hs = halves_stats(side, P, D, va, vb, z_h, fq, r)
            tot, ntr = total_trades(side, P, D, va, vb)
            res["cand"][name] = {"halves": hs, "verdict": verdict(hs, z_h), "total": tot, "trades": ntr, "vA": va, "vB": vb}
        out["sides"][side] = res
        print("== %s: испытаний %d (оси %d + расш. %d + совм. %d + кр. %d + буква %d + эталон %d), m=%d z=%.2f | конфигураций %d: >0 в обеих %d (≥10 сд. %d)" % (
            side, trials, n_stage1, n_ext, n_joint, int(have_cf), int(have_l), 2 * int(have_h), m, z_h, res["counts"]["configs"], res["counts"]["positive_both"], res["counts"]["positive_both_ge10"]))
        print("   cfg A→B:", cfgf[side]["cfg"]["A"], "\n   cfg B→A:", cfgf[side]["cfg"]["B"], "\n   joint:", cfgf[side]["joint"])
        for name, c in res["cand"].items():
            a, b = c["halves"]["A"], c["halves"]["B"]
            print("   %-26s A %+7.2f [%7.2f;%7.2f] w2d/c %+6.2f/%+6.2f | B %+7.2f [%7.2f;%7.2f] w2d/c %+6.2f/%+6.2f | tot %+9.2f n=%4d | %s" % (
                name, a["mean"], a["lo"], a["hi"], a["no_worst2_days"]["mean"], a["no_worst2_coins"]["mean"], b["mean"], b["lo"], b["hi"],
                b["no_worst2_days"]["mean"], b["no_worst2_coins"]["mean"], c["total"], c["trades"], c["verdict"]))
    json.dump(out, open(os.path.join(ROOT, "report-tgrid.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
