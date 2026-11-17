"""П-42 §13.12: отчёт по сетке тейков (перекос цены γ × перекос объёма κ, `pk` 0/1, источник NATR ns) и честный эталон «выбор на половине подбора».

На srv (каталоги прогонов — /data/p42-t566/out/tg1 (шаг 1: 24 клетки + ns=1 на базах), tg2 (ns=1 на выбранных клетках), pk (v13, гейт)):

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 takegrid_report.py select   → строки `ns=1` для выбранных клеток GK (stdout) + ns-cells.json
  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 takegrid_report.py full     → таблицы (stdout, takegrid-tables.md) + takegrid-report.json

Правила — docs/research/P-42-level-breakout.md §13.12 (записаны до счёта): выбор клетки на половине h по среднему $/сут. на h, сглаженному по окрестности
(пригодные клетки: ≥ 10 сделок на h); два чтения — «argmax» и «по §13.10» (Δ_h > 0 и плато на h); эталон: на A клетка c_B, на B — c_A.
Холм: m = 585 (лонг) / 574 (шорт) — верхняя граница §13.12.
"""
import filecmp
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import report_tick  # noqa: E402
import takegrid as tg  # noqa: E402
from lib import A, B, HALVES, ND, SIDES, load_all  # noqa: E402
from pk_report import read_rule  # noqa: E402
from report import fq_days  # noqa: E402

M = {"long": 585, "short": 574}
OUT = os.environ.get("TG_OUT", "/data/p42-t566/out")
MIN_TR = 10
BASE = (tg.GI0, tg.KI0)
FAMS = ("G", "K", "GK")
FAM_NAME = {"G": "G (γ при κ = 0)", "K": "K (κ при γ = 1)", "GK": "GK (γ×κ)"}


def cells(fam):
    if fam == "G":
        return [(g, tg.KI0) for g in range(len(tg.GAMMAS))]
    if fam == "K":
        return [(tg.GI0, k) for k in range(len(tg.KAPPAS))]
    return [(g, k) for g in range(len(tg.GAMMAS)) for k in range(len(tg.KAPPAS))]


def neighbors(fam, c):
    g, k = c
    if fam == "G":
        cand = [(g - 1, k), (g + 1, k)]
    elif fam == "K":
        cand = [(g, k - 1), (g, k + 1)]
    else:
        cand = [(g - 1, k), (g + 1, k), (g, k - 1), (g, k + 1)]
    cs = set(cells(fam))
    return [x for x in cand if x in cs]


def dist(c):
    return abs(c[0] - BASE[0]) + abs(c[1] - BASE[1])


def ident(c, pk, ns=False):
    return tg.cell_id(c[0], c[1], bool(pk), ns)


def pm(x):
    return ("%+.2f" % x).replace("-", "−")


def ci(st):
    return "%s [%s; %s]" % (pm(st["mean"]), ("%.2f" % st["lo"]).replace("-", "−"), ("%.2f" % st["hi"]).replace("-", "−"))


def select(D, T, side, pk, fam, h):
    """Выбор клетки семьи на половине h (§13.12 п. 3): (клетка, подробности, принята ли по чтению «по §13.10»)."""
    sl = HALVES[h]
    hi = 0 if h == "A" else 1
    cs = cells(fam)
    mean_h = {c: float(D[side][ident(c, pk)][sl].mean()) for c in cs}
    valid = {c: T[side][ident(c, pk)][hi] >= MIN_TR for c in cs}
    score = {}
    for c in cs:
        pool = [c] + neighbors(fam, c)
        score[c] = float(np.mean([mean_h[x] for x in pool if valid[x]])) if valid[c] else None
    cand = [c for c in cs if valid[c]]
    base_id = ident(BASE, pk)
    info = {"mean_h": {ident(c, pk): mean_h[c] for c in cs}, "score": {ident(c, pk): score[c] for c in cs},
            "valid": {ident(c, pk): valid[c] for c in cs}}
    if not cand:
        info["pick"] = base_id
        return BASE, info, False
    best = max(score[c] for c in cand)
    ties = [c for c in cand if abs(score[c] - best) < 1e-12]
    pick = min(ties, key=lambda c: (dist(c), cs.index(c)))
    info["pick"] = ident(pick, pk)
    if pick == BASE:
        info["accepted"] = False
        return pick, info, False
    dsel = D[side][ident(pick, pk)][sl] - D[side][base_id][sl]
    delta = float(dsel.mean())
    se = detstat.stat(dsel)["se"]
    nb = [n for n in neighbors(fam, pick) if n != BASE]
    dn = {ident(n, pk): float((D[side][ident(n, pk)][sl] - D[side][base_id][sl]).mean()) for n in nb}
    plateau = bool(all(v > 0 and v >= delta - se for v in dn.values()))
    info.update(delta=delta, se=se, plateau=plateau, nb_delta=dn)
    ok = delta > 0 and plateau
    info["accepted"] = bool(ok)
    return pick, info, ok


def selections(D, T):
    """sel[(side, pk, fam)] = {h: (клетка, подробности, принята)}."""
    out = {}
    for side in SIDES:
        for pk in (0, 1):
            for fam in FAMS:
                out[(side, pk, fam)] = {h: select(D, T, side, pk, fam, h) for h in ("A", "B")}
    return out


def cmd_select(D, T):
    sel = selections(D, T)
    ids = []
    rows = {}
    for side in SIDES:
        for pk in (0, 1):
            s = sel[(side, pk, "GK")]
            cA, cB = s["A"][0], s["B"][0]
            rows["%s|pk%d" % (side, pk)] = {"c_A": ident(cA, pk), "c_B": ident(cB, pk)}
            for c in (cA, cB):
                if c != BASE and ident(c, pk) not in ids:
                    ids.append(ident(c, pk))
    json.dump({"ns_cells": ids, "argmax_gk": rows}, open(os.path.join(OUT, "ns-cells.json"), "w"), ensure_ascii=False, indent=1)
    for ln in tg.ns_lines(ids):
        print(ln)
    sys.stderr.write("выбор GK (argmax): %s\n" % json.dumps(rows, ensure_ascii=False))


def cell_row(side, P, D, T, v, base_id, z, fq):
    hs = report_tick.halves_stats(side, P, D, v, v, z, fq, None)
    d = D[side][v] - D[side][base_id]
    dst = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
    return {"id": v, "halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][v].sum()),
            "trades": hs["A"]["trades"] + hs["B"]["trades"], "delta": dst, "delta_low_holm": dst["60"]["mean"] - z * dst["60"]["se"],
            "delta_read": read_rule(None, dst["A"], dst["B"], dst["60"], z)}


def honest(side, P, D, base_id, vA, vB, z, fq):
    """Эталон: на A — вариант vA (клетка, выбранная на B), на B — vB (выбранная на A)."""
    hs = report_tick.halves_stats(side, P, D, vA, vB, z, fq, None)
    dA = D[side][vA][A] - D[side][base_id][A]
    dB = D[side][vB][B] - D[side][base_id][B]
    dst = {"A": detstat.stat(dA), "B": detstat.stat(dB), "60": detstat.stat(np.concatenate([dA, dB]))}
    return {"vA": vA, "vB": vB, "halves": hs, "verdict": report_tick.verdict(hs, z), "delta": dst,
            "delta_low_holm": dst["60"]["mean"] - z * dst["60"]["se"],
            "delta_read": read_rule(None, dst["A"], dst["B"], dst["60"], z)}


def gate():
    out = {}
    for v in ("b0", "b0pk"):
        for side in SIDES:
            for kind in ("trades", "daily", "coinday"):
                f = "%s-%s.csv" % (side, kind)
                a, b = os.path.join(OUT, "tg1", v, f), os.path.join(OUT, "pk", v, f)
                out["%s/%s" % (v, f)] = os.path.exists(a) and os.path.exists(b) and filecmp.cmp(a, b, shallow=False)
    return out


def cmd_full(D, P, T):
    fq = fq_days()
    detstat.patch(report_tick)
    sel = selections(D, T)
    res = {"m": M, "gate_tg1_vs_v13": gate(), "cells": {}, "honest": {}, "ns": {}, "select": {}}
    g = res["gate_tg1_vs_v13"]
    print("== гейт: b0 и b0pk из tg1 (v14, дефолты) против прежних (v13) — %s" % (
        "ВСЁ СОВПАДАЕТ (%d файлов)" % len(g) if all(g.values()) else "РАСХОЖДЕНИЕ: %s" % [k for k, v in g.items() if not v]))
    md = []
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        print("\n########## %s: m = %d, z = %.3f" % (side, M[side], z))
        for pk in (0, 1):
            base_id = ident(BASE, pk)
            tag = "%s|pk%d" % (side, pk)
            rows = {}
            print("\n== %s, pk=%d: клетки сетки (γ×κ) — тики 4 мс, 566 монет" % (side, pk))
            md.append("\n#### %s, `pk=%d`: клетки сетки\n" % ("лонг" if side == "long" else "шорт", pk))
            md.append("| клетка | γ | κ | A $/сут [95 %] | сд. A | B $/сут [95 %] | сд. B | итог 60 сут., $ | сделок | §10 | Δ A | Δ B | Δ 60 [95 %] (нижняя после Холма) | чтение |")
            md.append("|---|---|---|---|---:|---|---:|---:|---:|---|---|---|---|---|")
            for c in cells("GK"):
                v = ident(c, pk)
                r = cell_row(side, P, D, T, v, base_id, z, fq)
                rows[v] = r
                a, b, d = r["halves"]["A"], r["halves"]["B"], r["delta"]
                print("  %-14s γ=%-3g κ=%-5g A %s n=%d | B %s n=%d | итог %+.2f $ сд. %d | %s | Δ A %s B %s 60 %s (низ. %.2f) %s" % (
                    v, tg.GAMMAS[c[0]], tg.KAPPAS[c[1]], ci(a), a["trades"], ci(b), b["trades"], r["total"], r["trades"], r["verdict"],
                    pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), r["delta_low_holm"], r["delta_read"]))
                md.append("| `%s` | %g | %g | %s | %d | %s | %d | %s | %d | %s | %s | %s | %s (%s) | %s |" % (
                    v, tg.GAMMAS[c[0]], tg.KAPPAS[c[1]], ci(a), a["trades"], ci(b), b["trades"], pm(r["total"]), r["trades"], r["verdict"],
                    pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), pm(r["delta_low_holm"]), r["delta_read"]))
            res["cells"][tag] = rows
            both = [(min(r["halves"]["A"]["mean"], r["halves"]["B"]["mean"]), k) for k, r in rows.items()
                    if r["halves"]["A"]["trades"] >= MIN_TR and r["halves"]["B"]["trades"] >= MIN_TR]
            top = sorted(both, reverse=True)[:3]
            print("  топ-3 по min(A, B) (≥ 10 сделок в каждой половине): %s" % ", ".join("%s %+.2f" % (k, x) for x, k in top))
            res["cells"][tag + "|top3_min_AB"] = [(k, x) for x, k in top]
            npos = sum(1 for r in rows.values() if r["halves"]["A"]["mean"] > 0 and r["halves"]["B"]["mean"] > 0)
            print("  клеток с $/сут. > 0 в A и в B: %d из %d" % (npos, len(rows)))
            res["cells"][tag + "|n_pos_both"] = npos
            # ns=1 на базе
            vns = ident(BASE, pk, True)
            if vns in D[side]:
                r = cell_row(side, P, D, T, vns, base_id, z, fq)
                res["ns"][tag + "|base"] = r
                a, b, d = r["halves"]["A"], r["halves"]["B"], r["delta"]
                print("  [ns=1, заглядывание] %-10s A %s n=%d | B %s n=%d | итог %+.2f $ | %s | Δ(ns=1 − ns=0) A %s B %s 60 %s %s" % (
                    vns, ci(a), a["trades"], ci(b), b["trades"], r["total"], r["verdict"], pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), r["delta_read"]))
            # честный эталон
            print("== %s, pk=%d: честный эталон (выбор на половине подбора, оценка вне выборки)" % (side, pk))
            md.append("\n#### %s, `pk=%d`: честный эталон (на A — клетка, выбранная на B; на B — выбранная на A)\n" % ("лонг" if side == "long" else "шорт", pk))
            md.append("| семья | чтение | c_A → B | c_B → A | A $/сут [95 %] | сд. A | B $/сут [95 %] | сд. B | §10 | Δ A | Δ B | Δ 60 [95 %] (нижняя после Холма) | чтение Δ |")
            md.append("|---|---|---|---|---|---:|---|---:|---|---|---|---|---|")
            for fam in FAMS:
                s = sel[(side, pk, fam)]
                for reading in ("argmax", "§13.10"):
                    cA = s["A"][0] if reading == "argmax" or s["A"][2] else BASE
                    cB = s["B"][0] if reading == "argmax" or s["B"][2] else BASE
                    r = honest(side, P, D, base_id, ident(cB, pk), ident(cA, pk), z, fq)
                    res["honest"]["%s|%s|%s" % (tag, fam, reading)] = {"cA": ident(cA, pk), "cB": ident(cB, pk), **r}
                    a, b, d = r["halves"]["A"], r["halves"]["B"], r["delta"]
                    print("  [%s, %s] c_A=%s (на B) c_B=%s (на A): A %s n=%d | B %s n=%d | %s | Δ A %s B %s 60 %s (низ. %.2f) %s" % (
                        fam, reading, ident(cA, pk), ident(cB, pk), ci(a), a["trades"], ci(b), b["trades"], r["verdict"],
                        pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), r["delta_low_holm"], r["delta_read"]))
                    md.append("| %s | %s | `%s` | `%s` | %s | %d | %s | %d | %s | %s | %s | %s (%s) | %s |" % (
                        FAM_NAME[fam], reading, ident(cA, pk), ident(cB, pk), ci(a), a["trades"], ci(b), b["trades"], r["verdict"],
                        pm(d["A"]["mean"]), pm(d["B"]["mean"]), ci(d["60"]), pm(r["delta_low_holm"]), r["delta_read"]))
                for h in ("A", "B"):
                    c, info, ok = s[h]
                    res["select"]["%s|%s|%s" % (tag, fam, h)] = {"pick": info["pick"], "accepted": ok,
                                                                "delta": info.get("delta"), "plateau": info.get("plateau"),
                                                                "nb_delta": info.get("nb_delta")}
                    print("      выбор на %s (%s): %s%s" % (h, fam, info["pick"], "" if c == BASE else
                                                          " Δ_%s %+.2f, плато %s, принят по §13.10: %s" % (h, info["delta"], info["plateau"], ok)))
            # ns=1 на выбранных клетках GK (argmax)
            s = sel[(side, pk, "GK")]
            cA, cB = s["A"][0], s["B"][0]
            vA, vB = ident(cB, pk, True), ident(cA, pk, True)
            if vA in D[side] and vB in D[side]:
                r0 = honest(side, P, D, base_id, ident(cB, pk), ident(cA, pk), z, fq)
                r1 = honest(side, P, D, base_id, vA, vB, z, fq)
                dA = D[side][vA][A] - D[side][ident(cB, pk)][A]
                dB = D[side][vB][B] - D[side][ident(cA, pk)][B]
                dd = {"A": detstat.stat(dA), "B": detstat.stat(dB), "60": detstat.stat(np.concatenate([dA, dB]))}
                res["ns"][tag + "|GK_argmax"] = {"ns0": r0, "ns1": r1, "delta_ns": dd}
                a, b = r1["halves"]["A"], r1["halves"]["B"]
                print("  [ns=1 на клетках GK argmax, заглядывание] c_A=%s c_B=%s: A %s n=%d | B %s n=%d | Δ(ns=1 − ns=0) A %s B %s 60 %s" % (
                    vB, vA, ci(a), a["trades"], ci(b), b["trades"], pm(dd["A"]["mean"]), pm(dd["B"]["mean"]), ci(dd["60"])))
    json.dump(res, open(os.path.join(OUT, "takegrid-report.json"), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    open(os.path.join(OUT, "takegrid-tables.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    # счётчик испытаний: клетки (22), эталоны (24), ns (≤ 6) — верхняя граница §13.12
    n_ns = sum(1 for k in D["long"] if k.endswith("_ns1"))
    print("\n== испытания: новых клеток 22, эталонов 24, ns-клеток посчитано %d (верхняя граница 6); m = %s" % (n_ns, M))


def main():
    cmd = sys.argv[1]
    roots = [os.path.join(OUT, d) for d in ("tg1", "tg2") if os.path.isdir(os.path.join(OUT, d))]
    D, P, T = load_all(roots)
    if cmd == "select":
        cmd_select(D, T)
    else:
        cmd_full(D, P, T)


if __name__ == "__main__":
    main()
