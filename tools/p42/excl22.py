"""П-42 §13.4 п. 9: выборка без 22 монет (точный пересчёт `--symbols`) рядом с полной вселенной → <ROOT>/report-excl22.json и печать.

  P42_ROOT=data/p42v2 python tools/p42/excl22.py
Каталоги: полная вселенная — минутки stage1/ext/joint/final, тики tick-lx4; без 22 — excl22-1m, excl22-tick; контроль — ctrl-1m, ctrl-tick (b0 на 566 монетах).
Кандидаты стороны: база b0; перекрёстный (cB_ на сутках A, cA_ на сутках B); топ-3 (top3.json). Холм: m = лонг 219 / шорт 211 (п. 9) для всех чисел.
Справочно: приближённое исключение (вычитание P&L 22 монет из полной базы) против точного — оценка смещения приближения для §13.7.
"""
import json
import os
import sys

os.environ.setdefault("P42_ROOT", "data/p42v2")
os.environ.setdefault("P42_FQ_COL", "first_day_ge_12M")
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

from lib import A, B, ND, ROOT, SIDES, load_all, read_coinday, read_trades  # noqa: E402
from phase import rng  # noqa: E402
from report import fq_days  # noqa: E402
from report_tick import halves_stats, verdict  # noqa: E402
import detstat  # noqa: E402
import report_tick  # noqa: E402
detstat.patch(report_tick)  # одинаковый ряд — одинаковый интервал во всех таблицах

M = {"long": 219, "short": 211}
EX = [x.strip() for x in open(os.path.join(ROOT, "excl22.txt"), encoding="utf-8") if x.strip()]
ENG = {  # движок → (каталоги полной вселенной, каталоги без 22, суффикс имён вариантов)
    "1m": ([os.path.join(ROOT, d) for d in ("stage1", "ext", "joint", "final")], [os.path.join(ROOT, "excl22-1m")], ""),
    "tick": ([os.path.join(ROOT, "tick-lx4")], [os.path.join(ROOT, "excl22-tick")], "_lx4"),
}


def cand_list(side, sfx, top):
    c = {"база": ("b0" + sfx, "b0" + sfx), "перекрёстный": ("cB_%s%s" % (side, sfx), "cA_%s%s" % (side, sfx))}
    for t in top[side]:
        c["топ-3 " + t["vid"]] = (t["vid"] + sfx, t["vid"] + sfx)
    return c


def total_and_trades(side, P, D, vA, vB):
    x = np.concatenate([D[side][vA][A], D[side][vB][B]])
    nA = sum(1 for t in read_trades(P[(side, vA)], side) if int(t["entry_day"]) < 30)
    nB = sum(1 for t in read_trades(P[(side, vB)], side) if int(t["entry_day"]) >= 30)
    return float(x.sum()), nA + nB


def main():
    r = rng()
    fq = fq_days()
    top = json.load(open(os.path.join(ROOT, "top3.json")))
    out = {"excluded": EX, "m": M, "res": {}, "approx": {}, "control": {}}
    for eng, (full_roots, ex_roots, sfx) in ENG.items():
        Df, Pf, _ = load_all(full_roots)
        De, Pe, _ = load_all(ex_roots)
        for side in SIDES:
            z_h = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
            for name, (va, vb) in cand_list(side, sfx, top).items():
                row = {}
                for uni, D, P in (("full", Df, Pf), ("excl22", De, Pe)):
                    if va not in D[side] or vb not in D[side]:
                        continue
                    hs = halves_stats(side, P, D, va, vb, z_h, fq, r)
                    tot, ntr = total_and_trades(side, P, D, va, vb)
                    row[uni] = {"halves": hs, "verdict": verdict(hs, z_h), "total": tot, "trades": ntr, "vA": va, "vB": vb}
                out["res"]["%s|%s|%s" % (eng, side, name)] = row
            # приближённое исключение базы (вычитание P&L 22 монет) против точного
            vb0 = "b0" + sfx
            if vb0 in Df[side] and vb0 in De[side]:
                coin = read_coinday(Pf[(side, vb0)], side) or {}
                xa = Df[side][vb0] - sum((coin[c] for c in EX if c in coin), np.zeros(60))
                tr = read_trades(Pf[(side, vb0)], side)
                out["approx"]["%s|%s" % (eng, side)] = {
                    "exact_A": float(De[side][vb0][A].mean()), "exact_B": float(De[side][vb0][B].mean()),
                    "approx_A": float(xa[A].mean()), "approx_B": float(xa[B].mean()),
                    "full_A": float(Df[side][vb0][A].mean()), "full_B": float(Df[side][vb0][B].mean()),
                    "trades_exact": sum(1 for _ in read_trades(Pe[(side, vb0)], side)),
                    "trades_approx": sum(1 for t in tr if t["sym"] not in set(EX)), "trades_full": len(tr),
                    "removed_net": float(sum(float(t["net"]) for t in tr if t["sym"] in set(EX))),
                    "max_abs_day_diff": float(np.abs(xa - De[side][vb0]).max())}
    # контроль: b0 на полном списке --symbols обязан быть побайтово прежним
    for eng, sfx, base_dirs in (("1m", "", os.path.join(ROOT, "stage1", "b0")), ("tick", "_lx4", os.path.join(ROOT, "tick-lx4", "b0_lx4"))):
        cdir = os.path.join(ROOT, "ctrl-" + eng, "b0" + sfx)
        res = {}
        for side in SIDES:
            for f in ("-trades.csv", "-daily.csv", "-coinday.csv"):
                a, b = os.path.join(base_dirs, side + f), os.path.join(cdir, side + f)
                res[side + f] = os.path.exists(a) and os.path.exists(b) and open(a, "rb").read() == open(b, "rb").read()
        out["control"][eng] = res
    json.dump(out, open(os.path.join(ROOT, "report-excl22.json"), "w", encoding="utf-8"), indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o), ensure_ascii=False)
    print("control (byte-identical):", {k: all(v.values()) for k, v in out["control"].items()})
    for key, row in out["res"].items():
        for uni, c in row.items():
            a, b = c["halves"]["A"], c["halves"]["B"]
            print("%-34s %-6s A %+7.2f [%7.2f;%7.2f] B %+7.2f [%7.2f;%7.2f] tot %+9.2f n=%4d | %s" % (
                key, uni, a["mean"], a["lo"], a["hi"], b["mean"], b["lo"], b["hi"], c["total"], c["trades"], c["verdict"]))
    for k, v in out["approx"].items():
        print("approx", k, {kk: round(vv, 3) for kk, vv in v.items()})


if __name__ == "__main__":
    main()
