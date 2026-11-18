"""П-42 §13.19: таблицы markdown для отчёта из report-s19.json (сетка) и report-s19c.json (сборка). Запуск: python stops_md.py <каталог с json>  → печать."""
import json
import os
import sys

D = sys.argv[1]
g = json.load(open(os.path.join(D, "report-s19.json"), encoding="utf-8"))
c = json.load(open(os.path.join(D, "report-s19c.json"), encoding="utf-8"))
SIDE = {"long": "Лонг", "short": "Шорт"}
KINDS = ("stop", "be", "trail", "tx", "rt", "forced")


def row(side, rep, cell, with_delta=True):
    r = rep[side]["cells"][cell]
    a, b = r["halves"]["A"], r["halves"]["B"]
    t = r["trades"]
    k = t["kinds"]
    base = "| %s | %d | %+.2f | %+.3f | %.1f | %+.2f [%.2f; %.2f] | %+.2f [%.2f; %.2f] | %s |" % (
        cell, t["n"], t["sum"], t["mean"], t["pos_share"] * 100, a["mean"], a["lo"], a["hi"], b["mean"], b["lo"], b["hi"], "/".join(str(k[x]) for x in KINDS))
    if not with_delta or cell == "ow_e":
        return base + " — | — | — | — |"
    p = rep[side]["pairs"][cell]["delta"]
    pr = rep[side]["pairs"][cell]
    rd = "не читается (не испытание)" if side == "long" and cell.startswith("es") else pr["read"]  # §13.19: результат лонга по es* не читается
    return base + " %+.2f | %+.2f | %+.2f [%.2f; %.2f] | %s |" % (p["A"]["mean"], p["B"]["mean"], p["60"]["mean"], p["60"]["lo"], p["60"]["hi"], rd)


HDR = ("| клетка | сделок | сумма, $ | среднее на сделку, $ | в плюсе, % | A, $/сут. [95 %] | B, $/сут. [95 %] | виды выхода stop/be/trail/tx/rt/forced | Δ_A | Δ_B | Δ за 60 сут. [95 %] | чтение §13.11 |\n"
       "|---|---:|---:|---:|---:|---|---|---|---:|---:|---|---|")
for side in ("long", "short"):
    print("\n#### %s: сетка (m = %d, z = %.3f)\n" % (SIDE[side], g[side]["m"], g[side]["z_holm"]))
    print(HDR)
    for cell in g[side]["cells"]:
        print(row(side, g, cell))
    print("\nВыбор по правилу §13.19 (подбор на A, подтверждение на B; маска ≥ 70 сделок в A и в B):\n")
    print("| семейство | выбранная клетка | Δ_A | Δ_B | решение |\n|---|---|---:|---:|---|")
    for fam, s in g[side]["selection"].items():
        if s["cell"]:
            print("| %s | %s | %+.2f | %+.2f | %s |" % (fam, s["cell"], s["dA"], s["dB"], s["why"]))
        else:
            print("| %s | — | — | — | %s |" % (fam, s["why"]))
    l2 = {k: v["lot2"] for k, v in g[side]["cells"].items() if v.get("lot2") is not None}
    if l2:
        print("\nВторой лот [сразу, позже, отменён, отказ]: " + "; ".join("%s %s" % (k, v) for k, v in l2.items()))
for side in ("long", "short"):
    print("\n#### %s: сборка (m = %d, z = %.3f)\n" % (SIDE[side], c[side]["m"], c[side]["z_holm"]))
    print(HDR)
    print(row(side, c, "ow_e"))
    print(row(side, c, "comb_" + side))
    r = c[side]["cells"]["comb_" + side]
    ro = c[side]["cells"]["ow_e"]
    print("\nВердикт §10: ow_e — %s; сборка — %s. Нижняя граница после Холма: ow_e A %.2f / B %.2f; сборка A %.2f / B %.2f." % (
        ro["verdict"], r["verdict"], ro["halves"]["A"]["low_holm"], ro["halves"]["B"]["low_holm"], r["halves"]["A"]["low_holm"], r["halves"]["B"]["low_holm"]))
    print("\nУстойчивость (п. 7 §13.4), A / B, $/сут. [95 %]:\n")
    print("| срез | ow_e A | ow_e B | сборка A | сборка B |\n|---|---|---|---|---|")
    for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
        f = lambda h: "%+.2f [%.2f; %.2f]" % (h[k]["mean"], h[k]["lo"], h[k]["hi"])
        print("| %s | %s | %s | %s | %s |" % (k, f(ro["halves"]["A"]), f(ro["halves"]["B"]), f(r["halves"]["A"]), f(r["halves"]["B"])))
    pr = c[side]["pairs"]["comb_" + side]
    d = pr["delta"]
    print("\nΔ(сборка − ow_e): A %+.2f [%.2f; %.2f]; B %+.2f [%.2f; %.2f]; 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s; исчезло %d сделок (%+.2f $), новых %d (%+.2f $)." % (
        d["A"]["mean"], d["A"]["lo"], d["A"]["hi"], d["B"]["mean"], d["B"]["lo"], d["B"]["hi"], d["60"]["mean"], d["60"]["lo"], d["60"]["hi"], pr["low_holm_60"], pr["read"],
        pr["gone"]["n"], pr["gone"]["usd"], pr["new"]["n"], pr["new"]["usd"]))
    if r.get("lot2") is not None:
        print("\nВторой лот [сразу, позже, отменён, отказ]: %s" % (r["lot2"],))
