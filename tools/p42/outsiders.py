"""П-42 v2 (приказ владельца 14.11: «список абсолютных аутсайдеров … монеты которые в минусе на всех клетках, отдельно лонг отдельно шорт»).
Описательно, без новых испытаний. По стороне, движку (1m / tick) и монете: сколько «клеток» (вариантов) с её сделками и в скольких из них P&L монеты < 0.

  P42_ROOT=data/p42v2 python tools/p42/outsiders.py lists   → outsiders-{long,short}.csv, outsiders-summary.json, excl-*.txt (списки символов для проверки исключением)
  P42_ROOT=data/p42v2 python tools/p42/outsiders.py excl    → outsiders-excl.{csv,json}: база без выбранных на одной половине монет, на другой половине (1м — точный пересчёт `runexcl.sh`; тик — вычитание P&L монет, приближённо)
Клетки 1m: все варианты stage1/ext/joint/final; клетки tick: все варианты каталога tick-tickfull. Минимум клеток с сделками монеты — MINC.
Аутсайдер-100 = в минусе во всех своих клетках; аутсайдер-90 = в минусе в ≥ 90 % (но не в 100 %).
Устойчивость (найдено на этих данных): выбор по половине A — те же определения по сделкам половины A; те же монеты смотрим в половине B (и наоборот).
"""
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

from lib import A, B, ROOT, SIDES, read_coinday, read_trades, stat  # noqa: E402
from phase import rng  # noqa: E402

MINC = 5
TICK = os.environ.get("P42_TICK", "tick-tickfull")  # каталог клеток тика
TICK_RE = os.environ.get("P42_TICK_RE")  # regex имён вариантов-клеток тика (по умолчанию — все)
TICK_BASE = os.environ.get("P42_TICK_BASE", "b0")  # вариант-база тика
OUT = os.environ.get("P42_OUT") or ROOT  # куда писать результаты (читаем клетки всегда из ROOT)
ENGINES = {"1m": ["stage1", "ext", "joint", "final"], "tick": [TICK]}


def cells(engine, side):
    """[(vid, {sym: [nA, netA, nB, netB]})] по всем вариантам движка, где у стороны есть сделки."""
    out = []
    for sub in ENGINES[engine]:
        for vdir in sorted(glob.glob(os.path.join(ROOT, sub, "*"))):
            if not os.path.exists(os.path.join(vdir, side + "-trades.csv")):
                continue
            if engine == "tick" and TICK_RE and not re.search(TICK_RE, os.path.basename(vdir)):
                continue
            d = defaultdict(lambda: [0, 0.0, 0, 0.0])
            for t in read_trades(vdir, side):
                h = 0 if int(t["entry_day"]) < 30 else 2
                d[t["sym"]][h] += 1
                d[t["sym"]][h + 1] += float(t["net"])
            if d:
                out.append((os.path.basename(vdir), dict(d)))
    return out


def tally(cs, half=None):
    """sym → [клеток с сделками, клеток в минусе, сделок, сумма P&L] по половине (None — вся)."""
    res = defaultdict(lambda: [0, 0, 0, 0.0])
    for _, d in cs:
        for s, (na, pa, nb, pb) in d.items():
            n, p = (na + nb, pa + pb) if half is None else ((na, pa) if half == "A" else (nb, pb))
            if n == 0:
                continue
            r = res[s]
            r[0] += 1
            r[1] += 1 if p < 0 else 0
            r[2] += n
            r[3] += p
    return res


def klass(r):
    if r[0] < MINC:
        return ""
    if r[1] == r[0]:
        return "100"
    if r[1] / r[0] >= 0.9:
        return "90"
    return ""


def base_of(engine, side):
    sub = "stage1" if engine == "1m" else TICK
    vdir = os.path.join(ROOT, sub, "b0" if engine == "1m" else TICK_BASE)
    d = defaultdict(lambda: [0, 0.0])
    if os.path.exists(os.path.join(vdir, side + "-trades.csv")):
        for t in read_trades(vdir, side):
            d[t["sym"]][0] += 1
            d[t["sym"]][1] += float(t["net"])
    return d


def is_o100(r):
    return r[0] >= MINC and r[1] == r[0]


def is_o90(r):
    return r[0] >= MINC and r[1] / r[0] >= 0.9


def cmd_lists():
    allc = [x.strip() for x in open(os.path.join(ROOT, "all566.txt"), encoding="utf-8") if x.strip()]
    summ = {}
    for side in SIDES:
        rows = []
        for eng in ENGINES:
            cs = cells(eng, side)
            if not cs:
                continue
            full, ta, tb = tally(cs), tally(cs, "A"), tally(cs, "B")
            base = base_of(eng, side)
            rows_e = []
            for s, r in full.items():
                b = base.get(s, [0, 0.0])
                rows_e.append({
                    "engine": eng, "sym": s, "cells": r[0], "cells_neg": r[1], "neg_share": round(r[1] / r[0], 4),
                    "trades_sum_cells": r[2], "net_sum_cells": round(r[3], 2), "mean_net_cell": round(r[3] / r[0], 3),
                    "base_trades": b[0], "base_net": round(b[1], 2),
                    "A_cells": ta[s][0] if s in ta else 0, "A_neg": ta[s][1] if s in ta else 0,
                    "B_cells": tb[s][0] if s in tb else 0, "B_neg": tb[s][1] if s in tb else 0,
                    "class": klass(r),
                })
            rows += rows_e
            o100 = [x for x in rows_e if x["class"] == "100"]
            o90 = [x for x in rows_e if x["class"] == "90"]
            summ[(side, eng)] = {
                "cells": len(cs), "coins_with_trades": len(rows_e), "coins_ge_minc": sum(1 for x in rows_e if x["cells"] >= MINC),
                "o100": len(o100), "o90": len(o90),
                "base_net_o100": round(sum(x["base_net"] for x in o100), 2), "base_net_o90": round(sum(x["base_net"] for x in o90), 2),
                "base_net_total": round(sum(x["base_net"] for x in rows_e), 2),
                "base_net_losers_sum": round(sum(x["base_net"] for x in rows_e if x["base_net"] < 0), 2),
                "base_trades_o100": sum(x["base_trades"] for x in o100), "base_trades_total": sum(x["base_trades"] for x in rows_e)}
            # устойчивость: выбор по одной половине → те же монеты в другой
            for hsel, hev in (("A", "B"), ("B", "A")):
                tsel, tev = (ta, tb) if hsel == "A" else (tb, ta)
                for tag, pick in (("100", is_o100), ("90", lambda r: is_o90(r) or is_o100(r))):
                    sel = sorted(s for s, r in tsel.items() if pick(r))
                    with_ev = [s for s in sel if s in tev and tev[s][0] >= 1]
                    ev_out = [s for s in with_ev if is_o90(tev[s]) or is_o100(tev[s])]
                    ev_neg = [s for s in with_ev if tev[s][3] < 0]
                    summ[(side, eng, hsel + "->" + hev, tag)] = {
                        "selected": len(sel), "with_trades_other_half": len(with_ev), "other_outsider90plus": len(ev_out),
                        "other_net_neg": len(ev_neg), "syms": sel,
                        "base_share_neg_other_half": round(sum(1 for r in tev.values() if r[0] >= 1 and r[3] < 0) / max(1, sum(1 for r in tev.values() if r[0] >= 1)), 4),
                        "net_other_half_sum_over_cells": round(sum(tev[s][3] for s in with_ev), 2)}
        rows.sort(key=lambda x: (x["engine"], -x["neg_share"], x["mean_net_cell"]))
        with open(os.path.join(OUT, "outsiders-%s.csv" % side), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    out = {"|".join(map(str, k)): v for k, v in summ.items()}
    json.dump(out, open(os.path.join(OUT, "outsiders-summary.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for k, v in out.items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "syms"})
    for key, v in out.items():
        parts = key.split("|")
        if len(parts) == 4 and parts[1] == "1m":
            ex = set(v["syms"])
            name = "excl-%s-%s-%s.txt" % (parts[0], parts[2].replace(">", ""), parts[3])
            with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
                f.write(",".join(s for s in allc if s not in ex))


def daily(vdir, side):
    return np.array([float(r["pnl"]) for r in csv.DictReader(open(os.path.join(vdir, side + "-daily.csv")))])


def cmd_excl():
    """База стороны без монет, выбранных на половине X, — на половине Y (и на X для справки): среднее $/сут. и парная разница с базой."""
    summ = json.load(open(os.path.join(OUT, "outsiders-summary.json"), encoding="utf-8"))
    r = rng()
    rows = []
    for side in SIDES:
        for eng in ENGINES:
            bdir = os.path.join(ROOT, "stage1" if eng == "1m" else TICK, "b0" if eng == "1m" else TICK_BASE)
            if not os.path.exists(os.path.join(bdir, side + "-daily.csv")):
                continue
            xb = daily(bdir, side)
            coin = read_coinday(bdir, side) or {}
            tb = read_trades(bdir, side)
            for sel in ("A->B", "B->A"):
                hx, hy = sel.split("->")
                for tag in ("100", "90"):
                    key = "|".join([side, eng, sel, tag])
                    if key not in summ:
                        continue
                    syms = set(summ[key]["syms"])
                    if eng == "1m":
                        edir = os.path.join(ROOT, "excl", "excl-%s-%s-%s-%s" % (side, hx, hy, tag), "b0")
                        xe = daily(edir, side)
                        te = read_trades(edir, side)
                        method = "точно (пересчёт без монет)"
                    else:
                        xe = xb - sum((coin[c] for c in syms if c in coin), np.zeros(60))
                        te = [t for t in tb if t["sym"] not in syms]
                        method = "приближённо (вычитание P&L монет, слоты не освобождаются)"
                    row = {"side": side, "engine": eng, "selected_on": hx, "evaluated_on": hy, "class": tag, "n_coins": len(syms),
                           "method": method}
                    for lab, h in (("eval", hy), ("sel", hx)):
                        sl = A if h == "A" else B
                        d = stat(xe[sl] - xb[sl], r)
                        nb = sum(1 for t in tb if (int(t["entry_day"]) < 30) == (h == "A"))
                        ne = sum(1 for t in te if (int(t["entry_day"]) < 30) == (h == "A"))
                        out = sum(float(t["net"]) for t in tb if t["sym"] in syms and (int(t["entry_day"]) < 30) == (h == "A"))
                        row.update({lab + "_half": h, lab + "_base_mean": round(float(xb[sl].mean()), 3),
                                    lab + "_excl_mean": round(float(xe[sl].mean()), 3), lab + "_delta": round(d["mean"], 3),
                                    lab + "_delta_lo": round(d["lo"], 3), lab + "_delta_hi": round(d["hi"], 3),
                                    lab + "_trades_base": nb, lab + "_trades_excl": ne,
                                    lab + "_outsiders_net_in_base": round(out, 2)})
                    rows.append(row)
    with open(os.path.join(OUT, "outsiders-excl.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json.dump(rows, open(os.path.join(OUT, "outsiders-excl.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for x in rows:
        print("%-5s %-4s %s→%s %-3s монет=%2d | на %s: база %+.2f → без %+.2f, Δ %+.2f [%.2f; %.2f], сделок %d→%d | (на %s, где выбраны: Δ %+.2f)  [%s]" % (
            x["side"], x["engine"], x["selected_on"], x["evaluated_on"], x["class"], x["n_coins"], x["eval_half"], x["eval_base_mean"],
            x["eval_excl_mean"], x["eval_delta"], x["eval_delta_lo"], x["eval_delta_hi"], x["eval_trades_base"], x["eval_trades_excl"],
            x["sel_half"], x["sel_delta"], x["method"][:7]))


if __name__ == "__main__":
    {"lists": cmd_lists, "excl": cmd_excl}[sys.argv[1]]()
