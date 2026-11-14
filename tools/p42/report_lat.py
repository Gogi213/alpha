"""П-42 §13.4 п. 6–7, §13.6: вердикты шага B при задержке 0 / 200 / 250 / 1000 мс (вход и выход) с устойчивостью опровержения → <ROOT>/report-lat.json и печать.

  P42_ROOT=data/p42v2 P42_FQ_COL=first_day_ge_12M P42_M='{"long":207,"short":199}' python tools/p42/report_lat.py tick-tickfull8 tick-lat
Обозначения вариантов: `<id>` — lat 0; `<id>_lat<N>` — задержка только входа; `<id>_lx<N>` — задержка входа и выходов (§13.6); основное чтение — `_lx4` (§13.4 п. 8, В-157). Новых испытаний нет (те же кандидаты).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import ND, ROOT, SIDES, load_all  # noqa: E402
from phase import rng  # noqa: E402
from report import fq_days  # noqa: E402
from report_tick import halves_stats, verdict  # noqa: E402

M = json.loads(os.environ.get("P42_M", '{"long":207,"short":199}'))
TAGS = [("lat 0", ""), ("вход+выход 4", "_lx4"), ("вход 200", "_lat200"), ("вход+выход 200", "_lx200"), ("вход 250", "_lat250"), ("вход+выход 250", "_lx250"),
        ("вход 1000", "_lat1000"), ("вход+выход 1000", "_lx1000")]


def main():
    roots = [os.path.join(ROOT, n) for n in sys.argv[1:]]
    D, P, T = load_all(roots)
    top = json.load(open(os.path.join(ROOT, "top3.json")))
    r = rng()
    fq = fq_days()
    out = {}
    for side in SIDES:
        z_h = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        cands = {"база": ("b0", "b0"), "перекрёстный": ("cB_" + side, "cA_" + side)}
        for t in top[side]:
            cands["топ-3 " + t["vid"]] = (t["vid"], t["vid"])
        # повторный вход (только lx200)
        re_c = {"повтор 1 база": ("rb0", "rb0"), "повтор 1 перекрёстный": ("rcB_" + side, "rcA_" + side),
                "повтор 2 база": ("r2b0", "r2b0"), "повтор 2 перекрёстный": ("r2cB_" + side, "r2cA_" + side)}
        res = {}
        print("== %s (Холм m=%d z=%.2f)" % (side, M[side], z_h))
        for name, (va, vb) in list(cands.items()) + list(re_c.items()):
            for tag_name, tag in TAGS:
                a_id, b_id = va + tag, vb + tag
                if a_id not in D[side] or b_id not in D[side]:
                    continue
                hs = halves_stats(side, P, D, a_id, b_id, z_h, fq, r)
                v = verdict(hs, z_h)
                res["%s|%s" % (name, tag_name)] = {"A": a_id, "B": b_id, "halves": hs, "verdict": v}
                a, b = hs["A"], hs["B"]
                print("  %-26s %-16s A %+7.2f [%7.2f;%7.2f] n=%3d w2d %+7.2f w2c %+7.2f | B %+7.2f [%7.2f;%7.2f] n=%3d w2d %+7.2f w2c %+7.2f | %s" % (
                    name, tag_name, a["mean"], a["lo"], a["hi"], a["trades"], a["no_worst2_days"]["mean"], a["no_worst2_coins"]["mean"],
                    b["mean"], b["lo"], b["hi"], b["trades"], b["no_worst2_days"]["mean"], b["no_worst2_coins"]["mean"], v))
        out[side] = res
    json.dump(out, open(os.path.join(ROOT, "report-lat.json"), "w"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
