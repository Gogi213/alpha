"""П-42 §13.8: перетитровка без 22 монет — кандидаты шага A (минутки) и шага B (тики 4 мс; справочно lat 0 и 200 мс) на 544 монетах рядом с полной вселенной → data/p42v2-544/report-544.json и печать.

  python tools/p42/retitr544.py
Полная вселенная (566): data/p42v2 (минутки stage1/ext/joint/final; тики tick-lx4, tick-tickfull8 (lat 0), tick-lat (200 мс)); 544: data/p42v2-544 (минутки stage1/ext/joint/final; тики tick-t544).
Кандидаты каждой вселенной — свои: перекрёстный (cB_ на сутках A, cA_ на сутках B) и топ-3 — выбраны заново на 544 (§13.8). Холм: m = лонг 331 / шорт 319 (оба столбца).
"""
import json
import os
import sys

os.environ.setdefault("P42_FQ_COL", "first_day_ge_12M")
os.environ.setdefault("P42_ROOT", "data/p42v2")
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

from lib import A, B, ND, SIDES, load_all, read_trades  # noqa: E402
from phase import rng  # noqa: E402
from report import fq_days  # noqa: E402
from report_tick import halves_stats, verdict  # noqa: E402
import detstat  # noqa: E402
import report_tick  # noqa: E402
detstat.patch(report_tick)  # одинаковый ряд — одинаковый интервал во всех таблицах

R566, R544 = "data/p42v2", "data/p42v2-544"
M = json.loads(os.environ.get("P42_M", '{"long":331,"short":319}'))
STEPS = (  # (метка, каталоги 566, суффикс 566, каталоги 544, суффикс 544)
    ("1m", [R566 + "/" + d for d in ("stage1", "ext", "joint", "final")], "", [R544 + "/" + d for d in ("stage1", "ext", "joint", "final")], ""),
    ("lx4", [R566 + "/tick-lx4"], "_lx4", [R544 + "/tick-t544"], "_lx4"),
    ("lat0", [R566 + "/tick-tickfull8"], "", [R544 + "/tick-t544"], ""),
    ("lx200", [R566 + "/tick-lat"], "_lx200", [R544 + "/tick-t544"], "_lx200"),
)


def cands(side, sfx, top):
    c = {"база": ("b0" + sfx, "b0" + sfx), "перекрёстный": ("cB_%s%s" % (side, sfx), "cA_%s%s" % (side, sfx))}
    for t in top[side]:
        c["топ-3 " + t["vid"]] = (t["vid"] + sfx, t["vid"] + sfx)
    return c


def total_trades(side, P, D, va, vb):
    x = np.concatenate([D[side][va][A], D[side][vb][B]])
    nA = sum(1 for t in read_trades(P[(side, va)], side) if int(t["entry_day"]) < 30)
    nB = sum(1 for t in read_trades(P[(side, vb)], side) if int(t["entry_day"]) >= 30)
    return float(x.sum()), nA + nB


def main():
    r = rng()
    fq = fq_days()
    top = {R566: json.load(open(R566 + "/top3.json")), R544: json.load(open(R544 + "/top3.json"))}
    out = {"m": M, "res": {}, "top3": {"566": top[R566], "544": top[R544]}}
    for tag, d566, s566, d544, s544 in STEPS:
        D6, P6, _ = load_all(d566)
        D5, P5, _ = load_all(d544)
        for side in SIDES:
            z_h = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
            c6, c5 = cands(side, s566, top[R566]), cands(side, s544, top[R544])
            for name in list(c5) + [k for k in c6 if k not in c5]:
                row = {}
                for uni, D, P, cc in (("full", D6, P6, c6), ("re544", D5, P5, c5)):
                    if name not in cc:
                        continue
                    va, vb = cc[name]
                    if va not in D[side] or vb not in D[side]:
                        continue
                    hs = halves_stats(side, P, D, va, vb, z_h, fq, r)
                    tot, ntr = total_trades(side, P, D, va, vb)
                    row[uni] = {"halves": hs, "verdict": verdict(hs, z_h), "total": tot, "trades": ntr, "vA": va, "vB": vb, "name": name}
                out["res"]["%s|%s|%s" % (tag, side, name)] = row
    # сверка с разделом 6 (§13.4 п. 9): те же варианты на тех же 544 монетах обязаны быть побайтово равны
    eq = {}
    for v, sub in (("b0", "stage1"), ("cA_short", "final"), ("cB_short", "final"), ("cA_long", "final"), ("cB_long", "final"), ("v_tf_240", "stage1"), ("v_n_bounce_3", "stage1"),
                   ("v_e_0p1", "stage1"), ("v_be_0", "stage1"), ("j_be_0__t_low_2", "joint"), ("j_be_0__t_low_3", "joint")):
        for eng, a, b in (("1m", R566 + "/excl22-1m/" + v, R544 + "/" + sub + "/" + v), ("tick", R566 + "/excl22-tick/" + v + "_lx4", R544 + "/tick-t544/" + v + "_lx4")):
            ok = True
            for side in SIDES:
                for f in ("-trades.csv", "-daily.csv", "-coinday.csv"):
                    pa, pb = os.path.join(a, side + f), os.path.join(b, side + f)
                    ok = ok and os.path.exists(pa) and os.path.exists(pb) and open(pa, "rb").read() == open(pb, "rb").read()
            eq["%s|%s" % (eng, v)] = bool(ok)
    out["equal_to_excl22"] = eq
    print("равны разделу 6:", sum(eq.values()), "из", len(eq))
    json.dump(out, open(R544 + "/report-544.json", "w", encoding="utf-8"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o), ensure_ascii=False)
    for key, row in out["res"].items():
        for uni, c in row.items():
            a, b = c["halves"]["A"], c["halves"]["B"]
            print("%-34s %-6s %-22s A %+7.2f [%7.2f;%7.2f] B %+7.2f [%7.2f;%7.2f] tot %+9.2f n=%4d | %s" % (
                key, uni, c["vA"][:22], a["mean"], a["lo"], a["hi"], b["mean"], b["lo"], b["hi"], c["total"], c["trades"], c["verdict"]))


if __name__ == "__main__":
    main()
