#!/usr/bin/env python3
"""П-42 §13.52 (TK-652): трейл лимиткой `tl` (TK-603, правило В-172 без денежной проверки) против баз h775 в $ — сетка `tl` ∈ {1, 2} × `rr` ∈ {1, 2, 3}.
  python3 tk652_run.py variants   — клетки по сторонам → res/variants52.json (без движка, без P&L)
  python3 tk652_run.py cells      — движок `breakout tick` замороженным бинарём TK-626 (`alpha-mk.396989e9`): строка базы + 6 клеток на сторону, окна wa / wb / wc
  python3 tk652_run.py tables     — G-base / G-dup / G-win, статистика клеток и парная Δ к базе → res/cells52-<сторона>.json (P&L: только после PASS критика)
  python3 tk652_run.py verdict    — правило §13.52 п. 7 (Холм m = 12, месяцы, устойчивость, сумма, плато по `rr`, шум) + п. 8 «не хуже» → res/verdict52.json
  python3 tk652_run.py report     — таблицы отчёта (Markdown) → res/report52.md
Драйвер, загрузка сделок и статистика — §13.43 (`tk643_run`, без изменений). Доллары — ставка 500 $; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан."""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK643_ROOT", "/data/p42-tk652")       # корень tk643_run → свой
import tk643_run as t43  # noqa: E402

ROOT = t43.ROOT
FROZEN_BIN = "/data/p42-tk616/alpha-mk.396989e9"
FROZEN_SHA = "470046d924392f0eab60ee730c43eb943c17913463857458e93b702127ac7fe0"
TK643 = "/data/p42-tk643"                                        # готовые клетки `tl = 0` при `rr` 1 / 3 (только описательно, п. 9)
SIDES = ("L", "S")
TL_VALS = (1, 2)
RR_VALS = (1, 2, 3)
RR_BASE = 2                                                      # умолчание движка (`params.rs` r_m 2000)
PRIOR_TRIALS = 312 + 80 + 2                                      # п. 7 (ж): TK-643 на h775 (та же сторона) + стоп/выходы на 566 + TK-603 (tl1, tl2)
NI_MARGIN = 0.05                                                 # п. 8: «не хуже» — нижняя 5 % граница Δ $/сделку ≥ −0,05
THREADS = 2                                                      # CPUQuota 200 %
TK643_BM = {"L": 0, "S": 1}                                      # bm базы стороны (§13.43 BASE_AX)


def cells(side):
    out = []
    for tl in TL_VALS:
        for r in RR_VALS:
            p = {"tl": tl}
            if r != RR_BASE:
                p["rr"] = r
            out.append(dict(cid="tl%d_rr%d" % (tl, r), tl=tl, rr=r, patch=p))
    return out


def neighbours(c, allc):
    """Соседи по `rr` при том же `tl` (п. 7 (е))."""
    i = RR_VALS.index(c["rr"])
    ids = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(RR_VALS):
            ids.append("tl%d_rr%d" % (c["tl"], RR_VALS[j]))
    return [x for x in ids if x in allc]


def tk643_tl0_dir(w, side, rr):
    return os.path.join(TK643, "cells", "%s-%s" % (w, side), "C_bm%d_rr%d_tj0_t_high5_q50" % (TK643_BM[side], rr))


def cmd_variants(a):
    res = {s: cells(s) for s in SIDES}
    for s in SIDES:
        t43.say("%s: %s" % (s, ", ".join("%s {%s}" % (c["cid"], t43.patch_str(c["patch"])) for c in res[s])))
    json.dump(res, open(t43.q("res", "variants52.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_cells(a):
    st, r5, _, _ = t43._srv()
    import tk604_run as r4
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        raise SystemExit("G-bin: sha256 бинаря ≠ замороженному TK-626")
    r4.BIN, r4.THREADS = FROZEN_BIN, THREADS
    u = r5.Uni("h775")
    for side in SIDES:
        v = [("base", t43.variant_line(r5, u, side, {}))] + [(c["cid"], t43.variant_line(r5, u, side, c["patch"])) for c in cells(side)]
        t43.run_variants(u, side, v, "tl")
    t43.say("cells готово")


def cmd_tables(a):
    st, r5, p8, r34 = t43._srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    for side in SIDES:
        bser, bk, bp = t43.load_cell(st, p8, u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        gser, gk, gp = t43.load_cell(st, p8, u, side, t43.load_dirs(st, side, "base"))
        gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(t43.np.allclose(gp, bp, atol=1e-6))
        if not gb:
            bad.append("G-base %s: строка базы ≠ готовой базе h775" % side)
        out = dict(side=side, base=p8.cell_stats(bser, bk, bp), gbase=gb, cells={}, tl0={}, forced_base=int(sum(1 for k in bk if k == "forced")))
        sig = {}
        bkeys = set(p8.keys_of(bser))
        for c in cells(side):
            ser, kinds, pnl = t43.load_cell(st, p8, u, side, t43.load_dirs(st, side, c["cid"]))
            r = t43.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            ck = set(p8.keys_of(ser))
            r.update(tl=c["tl"], rr_val=c["rr"], patch=c["patch"], same_set=bool(ck == bkeys), only_cell=len(ck - bkeys), only_base=len(bkeys - ck),
                     forced=int(sum(1 for k in kinds if k == "forced")))
            out["cells"][c["cid"]] = r
            sig.setdefault((tuple(p8.keys_of(ser)), tuple(t43.np.round(pnl, 6))), []).append(c["cid"])
        # п. 9 (описательно): та же сетка при `tl = 0` — готовые клетки TK-643 (`rr` 1 / 3; `rr` 2 = база); пара «tl=k против tl=0 при том же rr»
        for r_ in RR_VALS:
            if r_ == RR_BASE:
                s0 = bser
            else:
                s0, _, _ = t43.load_cell(st, p8, u, side, {w.name: (tk643_tl0_dir(w.name, side, r_), side) for w in st.TRADE_WINDOWS})
            for tl in TL_VALS:
                cs, ck_, cp_ = t43.load_cell(st, p8, u, side, t43.load_dirs(st, side, "tl%d_rr%d" % (tl, r_)))
                rr0 = t43.stat_row(st, p8, r34, cs, ck_, cp_, s0, ncoins)
                out["tl0"]["tl%d_rr%d" % (tl, r_)] = dict(delta=rr0["delta"], dmonths=rr0["dmonths"], base_n=len(s0.val), base_sum=float(s0.val.sum()))
        out["gdup"] = [v for v in sig.values() if len(v) > 1]
        json.dump(out, open(t43.q("res", "cells52-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        t43.say("== %s: база %d сделок, Σ %+.2f $; G-base %s; клеток %d; G-dup %s; forced база %d" % (
            side, out["base"]["n"], out["base"]["sum"], "ok" if gb else "ОШИБКА", len(out["cells"]), out["gdup"], out["forced_base"]))
    if bad:
        t43.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t43.say("tables готово")


def verdict(rows_by_side):
    """п. 7: Холм по всем 12 клеткам обеих сторон; ворота (а)–(ж) по стороне; п. 8: «не хуже» для правила `tl = 2` при `rr` базы."""
    ids = [(s, c["cid"]) for s in SIDES for c in cells(s)]
    rej = dict(zip(ids, t43.holm([rows_by_side[s][cid]["delta"]["p"] for s, cid in ids])))
    res = {"m": len(ids), "trials_noise": len(cells("L")) + PRIOR_TRIALS, "sides": {}}
    for s in SIDES:
        rows = rows_by_side[s]
        allc = {c["cid"]: c for c in cells(s)}
        dmap = {cid: rows[cid]["delta"]["d"] for cid in allc}
        out = {}
        for cid, c in allc.items():
            r, dl = rows[cid], rows[cid]["delta"]
            g = {}
            g["a"] = r["n"] >= t43.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t43.MIN_MONTH))
            g["b"] = bool(rej[(s, cid)])
            g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t43.NEED_MONTHS
            g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
            g["e"] = dl["s_c"] > 0 and dl["dsum"] >= -1.0 * dl["se_sum"]
            nb = neighbours(c, allc)
            g["f"] = dl["d"] > 0 and bool(nb) and all(dmap[x] >= 0.5 * dl["d"] for x in nb)
            nz = t43.noise_bar(r["sharpe"], res["trials_noise"])
            g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
            out[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
        passed = [cid for cid in allc if out[cid]["passed"]]
        rule = rows["tl2_rr%d" % RR_BASE]["delta"]
        ni = bool(rule["lower"] >= -NI_MARGIN)
        res["sides"][s] = dict(cells=out, passed=passed,
                               answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база стороны остаётся",
                               rule_tl2=dict(d=rule["d"], lower=rule["lower"], margin=-NI_MARGIN, not_worse=ni,
                                             answer="tl=2 не хуже базы (нижняя граница ≥ −%.2f $/сделку)" % NI_MARGIN if ni else
                                             "не доказано, что tl=2 не хуже базы (нижняя граница < −%.2f $/сделку)" % NI_MARGIN))
    return res


def cmd_verdict(a):
    rows = {s: json.load(open(t43.q("res", "cells52-%s.json" % s), encoding="utf-8"))["cells"] for s in SIDES}
    res = verdict(rows)
    json.dump(res, open(t43.q("res", "verdict52.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    for s, v in res["sides"].items():
        t43.say("== %s: m = %d, N шума = %d; %s; правило: %s" % (s, res["m"], res["trials_noise"], v["answer"], v["rule_tl2"]["answer"]))
        for g in "abcdefg":
            t43.say("   (%s) прошли %d из %d" % (g, sum(1 for c in v["cells"].values() if c["gates"][g]), len(v["cells"])))


def f(x, d=2):
    return "—" if x is None else ("%+." + str(d) + "f") % x


def cmd_report(a):
    v = json.load(open(t43.q("res", "verdict52.json"), encoding="utf-8"))
    L = []
    for s in SIDES:
        tab = json.load(open(t43.q("res", "cells52-%s.json" % s), encoding="utf-8"))
        b = tab["base"]
        rr0 = b["rr"]
        L += ["### %s" % {"L": "Лонг `L_base`", "S": "Шорт `S_base`"}[s], "",
              "База h775: %d сделок, Σ %s $, %s $/сделку, Sharpe %.2f; R:R %.2f; стопов %d, средний убыток стопа %s $ = %.2f %% маржи при плече 2; трейл-выходов %d (%.0f %%)." % (
                  b["n"], f(b["sum"]), f(b["mean"], 3), b["sharpe"], rr0["rr"], rr0["stops"]["n"], f(rr0["stops"]["mean_loss"]), rr0["stops"]["mean_loss_pct"],
                  rr0["kinds"].get("trail", {}).get("n", 0), 100 * rr0["kinds"].get("trail", {}).get("share", 0.0)), "",
              "| клетка | сделок | Σ $ | $/сделку | Sharpe | R:R | трейл-выходов | Δ $/сделку [нижн. 5 %] | p | ΔΣ $ ± SE | Δ $/сделку по месяцам | сделки = базе | ворота a–g | ответ |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for cid, r in tab["cells"].items():
            d, g = r["delta"], v["sides"][s]["cells"][cid]
            L.append("| `%s` | %d | %s | %s | %.2f | %.2f | %d | %s [%s] | %.4f | %s ± %.1f | %s | %s | %s | %s |" % (
                t43.patch_str(r["patch"]), r["n"], f(r["sum"]), f(r["mean"], 3), r["sharpe"], r["rr"]["rr"], r["kinds_n"].get("trail", 0),
                f(d["d"], 3), f(d["lower"], 3), d["p"], f(d["dsum"], 1), d["se_sum"], " / ".join(f(x, 1) for x in r["dmonths"]),
                "да" if r["same_set"] else "нет (+%d / −%d)" % (r["only_cell"], r["only_base"]),
                "".join(k if ok else "·" for k, ok in g["gates"].items()), "**да**" if g["passed"] else "нет"))
        L += ["", "Шумовая планка Sharpe (N = %d): %s." % (v["trials_noise"], "; ".join("%s %.2f" % (cid, c["noise"]["noise_bar"]) for cid, c in v["sides"][s]["cells"].items())),
              "", "Ответ стороны: %s. Правило `tl = 2` при `rr = 2`: %s (Δ %s $/сделку, нижняя граница %s)." % (
                  v["sides"][s]["answer"], v["sides"][s]["rule_tl2"]["answer"], f(v["sides"][s]["rule_tl2"]["d"], 3), f(v["sides"][s]["rule_tl2"]["lower"], 3)),
              "", "Описательно (п. 9): `tl = k` против `tl = 0` при том же `rr` (`rr` 1 / 3 — готовые клетки TK-643):", "",
              "| клетка | Δ $/сделку [нижн. 5 %] | ΔΣ $ ± SE | Δ $/сделку по месяцам |", "|---|---|---|---|"]
        for cid, r in tab["tl0"].items():
            d = r["delta"]
            L.append("| `%s` против `tl0_rr%s` | %s [%s] | %s ± %.1f | %s |" % (cid, cid[-1], f(d["d"], 3), f(d["lower"], 3), f(d["dsum"], 1), d["se_sum"],
                                                                            " / ".join(f(x, 1) for x in r["dmonths"])))
        L.append("")
    open(t43.q("res", "report52.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict, "report": cmd_report}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
