"""П-42 §13.5: числа по вариантам «повторный вход по уровню» (reentry=1 — стоп расходует уровень, reentry=2 — выходы не расходуют) → <ROOT>/report-reentry.json и печать.

  P42_ROOT=data/p42v2 P42_FQ_COL=first_day_ge_12M P42_M='{"long":207,"short":199}' python tools/p42/report_reentry.py tick-tickfull8 tick-reentry tick-reentry2
Первый каталог — база без re-entry (`b0`), остальные — варианты (`rb0`, `rcB_/rcA_` — reentry=1; `r2b0`, `r2cB_/r2cA_` — reentry=2).
Кандидаты стороны: база с re-entry; перекрёстный (rcB_ на A, rcA_ на B) с re-entry; справочно lat 250/1000. Холм — по накопленному счётчику §13.5.
"""
import json
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from lib import A, B, ND, ROOT, SIDES, load_all, read_trades, stat  # noqa: E402
from phase import rng  # noqa: E402
from report import fq_days  # noqa: E402
from report_tick import halves_stats, verdict  # noqa: E402

M = json.loads(os.environ.get("P42_M", '{"long":207,"short":199}'))
PREFIX = {"reentry1": ("rb0", "rc"), "reentry2": ("r2b0", "r2c")}


def repeats(vdir, side, h):
    """Сделки половины h: всего, уровней с ≥ 2 сделками, доля сделок-повторов (вторая и далее по уровню)."""
    tr = [t for t in read_trades(vdir, side) if (int(t["entry_day"]) < 30) == (h == "A")]
    per = Counter((t["sym"], t["level"]) for t in tr)
    n = len(tr)
    return {"trades": n, "levels": len(per), "levels_ge2": sum(1 for v in per.values() if v >= 2),
            "repeat_share": (n - len(per)) / n if n else 0.0}


def main():
    names = sys.argv[1:]
    roots = [os.path.join(ROOT, n) for n in names]
    D, P, T = load_all(roots)
    r = rng()
    fq = fq_days()
    out = {}
    for side in SIDES:
        z_h = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        cands = {"b0 (без re-entry)": ("b0", "b0")}
        for tag, (b, c) in PREFIX.items():
            if b in D[side]:
                cands[tag + " база"] = (b, b)
            if c + "B_" + side in D[side] and c + "A_" + side in D[side]:
                cands[tag + " перекрёстный"] = (c + "B_" + side, c + "A_" + side)
        res = {"m": M[side], "z_holm": z_h, "cand": {}, "sens": {}}
        print("== %s: Холм m=%d z=%.2f" % (side, M[side], z_h))
        for k, (vA, vB) in cands.items():
            hs = halves_stats(side, P, D, vA, vB, z_h, fq, r)
            rp = {h: repeats(P[(side, v)], side, h) for h, v in (("A", vA), ("B", vB))}
            res["cand"][k] = {"vA": vA, "vB": vB, "halves": hs, "repeats": rp, "verdict": verdict(hs, z_h)}
            a, b = hs["A"], hs["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d (%.2f/сут) lowH %.2f | B %+.2f [%.2f; %.2f] n=%d (%.2f/сут) lowH %.2f | стопов %.0f%%/%.0f%% | повторов %.0f%%/%.0f%% (уровней ≥2 сд.: %d/%d)" % (
                k, res["cand"][k]["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], a["trades_per_day"], a["low_holm"],
                b["mean"], b["lo"], b["hi"], b["trades"], b["trades_per_day"], b["low_holm"], a["stop_share"] * 100,
                b["stop_share"] * 100, rp["A"]["repeat_share"] * 100, rp["B"]["repeat_share"] * 100,
                rp["A"]["levels_ge2"], rp["B"]["levels_ge2"]))
            for h in ("A", "B"):
                x = hs[h]
                print("      %s: без 2 лучших суток %+.2f | без 2 лучших монет %+.2f | после 1-го дня ≥12M %+.2f | худшие сутки %.1f | KPI %s" % (
                    h, x["no_best2_days"]["mean"], x["no_best2_coins"]["mean"], x["after_first_day"]["mean"], x["worst_day"], x["kpi"]))
        # справочно: задержка
        for tag, (b, c) in PREFIX.items():
            for lat in ("lat250", "lat1000"):
                for nm, (va, vb) in (("база", (b + "_" + lat, b + "_" + lat)),
                                     ("перекрёстный", (c + "B_%s_%s" % (side, lat), c + "A_%s_%s" % (side, lat)))):
                    if va in D[side] and vb in D[side]:
                        e = {}
                        for h, vid, sl in (("A", va, A), ("B", vb, B)):
                            x = D[side][vid][sl]
                            st = stat(x, r)
                            rp = repeats(P[(side, vid)], side, h)
                            e[h] = {"mean": float(x.mean()), "lo": st["lo"], "hi": st["hi"], "trades": rp["trades"],
                                    "trades_per_day": rp["trades"] / 30}
                        res["sens"]["%s %s %s" % (tag, nm, lat)] = e
                        print("   sens %s %s %s: A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d" % (
                            tag, nm, lat, e["A"]["mean"], e["A"]["lo"], e["A"]["hi"], e["A"]["trades"],
                            e["B"]["mean"], e["B"]["lo"], e["B"]["hi"], e["B"]["trades"]))
        out[side] = res
    json.dump(out, open(os.path.join(ROOT, "report-reentry.json"), "w"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
