#!/usr/bin/env python3
"""TK-063: П-12 §9 по В-208 (Судья reviews/P-12-2026-10-08-sharpe-rule.md) — ΔSR к базе, R1 (154 кл.) + R2 v2 (40 кл.) одним анализом.
Ряд — $_норм по суткам закрытия (closes cap0 / cap3 для B2); загрузка — головы p12-r1-analyze.py и p12-r2-analyze.py (exec до бутстрепа).
Ворота: п.1 ΔSR>0 (парный блочный бутстреп, блоки общие, max-T WY в семье, Холм m=42, p<=α_k/2); п.2 С3 по месяцам (верх 95% ΔSR_мес>=0);
п.3 С2 (без 2 суток с наибольшей парной разностью; монета — closes без символа, считается для прошедших отдельно); п.4 >=6 из 8 месяцев.
Порядок: ярусы по SR (бутстреп лидер−i, Холм, α=0,05), затем k_c, K_c, SR. Выход docs/findings/p12-sharpe-<free|B2>-2026-10-08.csv + печать."""
import csv, math, os, sys
from p12lib import ONLY_R2, OUT, M_FAM, analyse, load, pack, pack_ext

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"


def main():
    A, fa = ({}, {}) if os.environ.get("P12_ONLY") == "r2" else analyse(pack(load("tools/compute/p12-r1-analyze.py", MODE), True), 63)
    Bz, fb = analyse(pack(load("tools/compute/p12-r2-analyze.py", MODE), False), 64)
    Ex, fe = ({}, {}) if ONLY_R2 else analyse(pack_ext(MODE), 65)
    res = {**{c: dict(r, pk="R1") for c, r in A.items() if r}, **{c: dict(r, pk="R2") for c, r in Bz.items() if r}, **{c: dict(r, pk="ext") for c, r in Ex.items() if r}}
    fams = {**fa, **fb, **fe}
    famp = {f: min(res[c]["pwy"] for c in cs) for f, cs in fams.items()}
    famh = {f: min(res[c]["pharm"] for c in cs) for f, cs in fams.items()}
    holm = {}
    for store, nm in ((famp, "польза"), (famh, "вред")):
        alive = True
        for k, (f, p) in enumerate(sorted(store.items(), key=lambda kv: kv[1])):
            thr = 0.025 / (M_FAM - k); alive = alive and p <= thr; holm[(nm, f)] = (p, thr, alive)
    print(f"== П-12 Шарп (В-208), режим {MODE}; клеток R1 {sum(1 for r in res.values() if r['pk']=='R1')} R2 {sum(1 for r in res.values() if r['pk']=='R2')} ext {sum(1 for r in res.values() if r['pk']=='ext')}; семей {len(fams)}, m={M_FAM}")
    rows = []
    for c, r in res.items():
        g1 = bool(holm[("польза", r["fam"])][2] and r["pwy"] <= holm[("польза", r["fam"])][1])
        bad_m = [m for m, v in r["mon"].items() if v[1] < 0]
        g2 = not bad_m
        g3 = bool(r["c2"][0] >= 0 and r["c2"][1] >= 0)
        g4 = len(r["dm"]) >= 6
        passed = g1 and g2 and g3 and g4
        refut = bool(holm[("вред", r["fam"])][2] and r["pharm"] <= holm[("вред", r["fam"])][1])
        fails = [i for i, g in ((1, g1), (2, g2), (3, g3), (4, g4)) if not g]
        r.update(g1=g1, g2=g2, g3=g3, g4=g4, passed=passed, refut=refut, bad_m=bad_m)
        rows.append([MODE, r["pk"], c, r["fam"], r["base"], round(r["sr"], 4), round(r["sr"] * math.sqrt(365), 2), round(r["srb"], 4), round(r["dsr"], 4),
                     round(r["lo"], 4), round(r["hi"], 4), round(r["T"], 3), round(r["pwy"], 4), round(r["pharm"], 4), r["k"], len(r["dm"]), round(r["K"], 4),
                     round(r["usd"] - r["usdb"], 1), int(g1), int(g2), int(g3), int(g4), int(passed), int(refut), ";".join(map(str, fails)),
                     ";".join(bad_m), round(r["c2"][0], 4), round(r["c2"][1], 4)])
    print("семьи (польза/вред), p < 0,3: порог Холма α/2/(42-k)")
    for (nm, f), (p, thr, ok) in sorted(holm.items(), key=lambda kv: (kv[0][0], kv[1][0])):
        if p < 0.3:
            print(f"  {nm} {f}: p={p:.4f} thr={thr:.2e} {'ПРОХОДИТ' if ok else 'нет'}")
    print("прошли ворота:", [c for c, r in res.items() if r["passed"]])
    print("опровергнуто (вред п.1):", [c for c, r in res.items() if r["refut"]])
    for key, nm in (("g1", "п.1"), ("g2", "п.2"), ("g3", "п.3"), ("g4", "п.4")):
        print(nm, "проходят:", sum(1 for r in res.values() if r[key]))
    for r in sorted(res.values(), key=lambda r: -r["T"])[:10]:
        print(f"топ T: {r['c']} SR{r['sr']:+.3f} (B {r['srb']:+.3f}) ΔSR{r['dsr']:+.3f}[{r['lo']:+.3f};{r['hi']:+.3f}] T{r['T']:.2f} pWY{r['pwy']:.3f} k={r['k']}/{len(r['dm'])} K={r['K']:.3f}")
    for r in sorted(res.values(), key=lambda r: r["T"])[:6]:
        print(f"низ T: {r['c']} SR{r['sr']:+.3f} (B {r['srb']:+.3f}) ΔSR{r['dsr']:+.3f}[{r['lo']:+.3f};{r['hi']:+.3f}] T{r['T']:.2f} pharm{r['pharm']:.3f}")
    print("ΔSR>0:", sum(r["dsr"] > 0 for r in res.values()), "из", len(res), "; нижняя 95%>0:", sum(r["lo"] > 0 for r in res.values()),
          "; верх<0:", sum(r["hi"] < 0 for r in res.values()))
    ranked = sorted(res.values(), key=lambda r: (-r["k"], r["K"], -r["sr"]))
    print("справочно (k_c, K_c, SR; без ярусов и ворот):", [(r["c"], r["k"], round(r["K"], 3), round(r["sr"], 3)) for r in ranked[:5]])
    with open(f"docs/findings/p12-sharpe-{MODE}-{OUT}.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mode", "pack", "cell", "family", "base", "sr_day", "sr_year", "sr_base", "d_sr", "ci_lo", "ci_hi", "T", "p_wy", "p_harm_wy", "k_months_sr_pos",
                    "months_defined", "kpi_share", "d_usd", "gate1", "gate2", "gate3", "gate4", "passed", "refuted", "failed_gates", "months_dsr_hi_neg", "c2_dsr", "c2_lo"])
        w.writerows(sorted(rows, key=lambda r: -r[11]))


main()
