"""TK-620 шаг 0 (§13.31): описательные кривые «где отменяется сценарий» по дампам `P42_EXC` (`<side>-exc.csv`) и механическое правило постановки сетки.
  python tk620_step0.py --root /data/p42-tk620/exc --variant L_base --side long --out <отчёт.md> [--json <значения.json>]
Каталоги: `<root>/<окно>/<variant>/<side>-exc.csv`, окна `wa/wb/wc` (`retitr_full.WINDOWS`). Берутся только зачётные сутки (дата сигнала в [lo, hi] окна);
сутки позже 13.11 не читаются. P&L нет. Правило постановки — `rule_levels` / `rule_margin` / `rule_width` ровно как записано в §13.31 п. 3; менять после чтения кривой нельзя.
Единица кривой «уровень»: d = 0,25·k NATR ниже цены уровня; кривая «край»: d = 0,25·k NATR ниже дальнего края проторговки (k = 0 — на краю)."""
import argparse
import csv
import datetime
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402

STEP = 0.25
NSTEP = 20
NMIN = 30                      # n на точку кривой, меньше — точка не читается
DAY_MS = 86_400_000
SCALE = 1e8
SIDES = {"long": 1, "short": -1}


def load(root, variant, side):
    """Строки дампа по зачётным суткам окон: dict колонок (numpy) + индекс окна и месяца."""
    cols, win_of, mon_of = {}, [], []
    s = SIDES[side]
    for w in rf.WINDOWS:
        path = os.path.join(root, w.name, variant, side + "-exc.csv")
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            day = w.t0d + datetime.timedelta(days=int(r["sig_ms"]) // DAY_MS)
            if not (w.lod <= day <= w.hid):
                continue
            for k, v in r.items():
                cols.setdefault(k, []).append(v)
            win_of.append(w.name)
            mon_of.append((day.year - 2026) * 12 + day.month - 7)
    out = {k: v for k, v in cols.items()}
    n = len(win_of)
    num = {}
    for k in ("sig_ms", "sig_min", "dist_e8", "lp_e8", "natr_e8", "nvalid_rng", "touch", "up1"):
        num[k] = np.array([int(x) for x in out[k]], dtype=np.int64)
    for k in [f"l{i}" for i in range(1, NSTEP + 1)] + [f"r{i}" for i in range(0, NSTEP + 1)]:
        num[k] = np.array([int(x) for x in out[k]], dtype=np.int64)
    for k in ("rlo_e8", "rhi_e8"):
        num[k] = np.array([int(x) if x != "" else 0 for x in out[k]], dtype=np.int64)
    num["win"] = np.array(win_of)
    num["mon"] = np.array(mon_of, dtype=np.int64)
    # единица u = |L|·NATR (e8) и начальная глубина сигнала под уровнем в NATR (dist — доля цены уровня, e8)
    lp = np.abs(num["lp_e8"]).astype(float)
    nat = num["natr_e8"].astype(float)
    num["u"] = lp * nat / SCALE
    num["init_lvl"] = num["dist_e8"] / np.maximum(nat, 1.0)
    # начальная глубина под дальним краем (в позиционной раме): (rlo − цена сигнала)/u; цена сигнала = L − dist·L
    rlo_pf = s * num["rlo_e8"].astype(float)
    psig_pf = s * num["lp_e8"].astype(float) - num["dist_e8"] / SCALE * lp
    num["init_edge"] = (rlo_pf - psig_pf) / np.maximum(num["u"], 1.0)
    num["width"] = (num["rhi_e8"] - num["rlo_e8"]).astype(float) / np.maximum(num["u"], 1.0) * 1.0
    num["width"] = np.abs(num["width"])
    assert n == len(num["touch"])
    return num


def curve(num, kind):
    """Кривая по k = 0…20 (d = 0,25·k NATR): строки (k, d, n, n_касаний, n_продолжений) среди «достигших» d до касания; база p0 — отдельно (`base_p`).
    'lvl': достигших l_k среди сигналов, начальная глубина которых < d (позиция жива при стопе на d); k = 0 — все сигналы.
    'edge': достигших r_k (r_0 — на краю) среди сигналов с проторговкой и ценой сигнала выше края на d; база — все сигналы с проторговкой."""
    touch = num["touch"] >= 0
    up1 = num["up1"] >= 0
    base = np.ones(len(touch), dtype=bool) if kind == "lvl" else num["nvalid_rng"] == 1
    rows = []
    for k in range(0, NSTEP + 1):
        d = STEP * k
        if kind == "lvl":
            reach = base if k == 0 else base & (num["init_lvl"] < d) & (num["l%d" % k] >= 0)
        else:
            reach = base & (num["init_edge"] < d) & (num["r%d" % k] >= 0)
        rows.append((k, d, int(reach.sum()), int((reach & touch).sum()), int((reach & up1).sum())))
    bn = int(base.sum())
    base_p = (int((base & touch).sum()) / bn) if bn else float("nan")
    return rows, base_p, bn


def first_halving(rows, p0, frac, nmin=NMIN):
    """Первое d, где p_touch ≤ frac·p0 на двух соседних шагах подряд при n ≥ nmin; None — нет."""
    pts = {k: (n, nt) for k, d, n, nt, nu in rows}
    for k in range(1, NSTEP):
        a, b = pts.get(k), pts.get(k + 1)
        if a and b and a[0] >= nmin and b[0] >= nmin and a[1] / a[0] <= frac * p0 and b[1] / b[0] <= frac * p0:
            return STEP * k
    return None


def q4(x):
    """Округление до 0,25 NATR, половина — вверх (детерминированно)."""
    return math.floor(x * 4 + 0.5) / 4.0


def q3(x):
    """Округление до 0,001 NATR (разрешение ключей движка), половина — вверх."""
    return math.floor(x * 1000 + 0.5) / 1000.0


def levels_grid(xh, xq):
    """§13.31 п. 3.1: значения `nx`/`sl` — {X_h', середина, X_q'}; X_q' − X_h' ≥ 0,5 ⇒ середина строго между концами (три разных значения)."""
    if xh is None:
        return [2.0, 3.5, 5.0]
    xh1 = min(max(xh, 1.0), 4.5)
    xq1 = 5.0 if xq is None else min(max(xq, xh1 + 0.5), 5.0)
    mid = q4((xh1 + xq1) / 2.0)
    assert xh1 < mid < xq1, (xh, xq, xh1, mid, xq1)
    return [xh1, mid, xq1]


def margin_grid(mh, mq):
    """§13.31 п. 3.2: значения `cm` — {M_h', M_q'}; M_h' ≤ 4,75, M_q' ≥ M_h' + 0,25 (два разных значения)."""
    if mh is None:
        return [0.5, 1.5]
    mh1 = min(max(mh, 0.25), 4.75)
    mq1 = 5.0 if mq is None else min(max(mq, mh1 + 0.25), 5.0)
    assert mh1 < mq1, (mh, mq, mh1, mq1)
    return [mh1, mq1]


def rule_levels(rows, p0):
    xh = first_halving(rows, p0, 0.5)
    xq = first_halving(rows, p0, 0.25)
    note = "" if xh is not None else "p не упала до p0/2 при n ≥ 30 до 5 NATR"
    return dict(xh=xh, xq=xq, grid=levels_grid(xh, xq), note=note)


def rule_margin(rows, p0):
    mh = first_halving(rows, p0, 0.5)
    mq = first_halving(rows, p0, 0.25)
    note = "" if mh is not None else "p не упала до p0/2 при n ≥ 30 до 5 NATR от края"
    return dict(mh=mh, mq=mq, grid=margin_grid(mh, mq), note=note)


def rule_width(num):
    """§13.31 п. 3.3: квантили ширины проторговки в NATR по сигналам с проторговкой: q10, q25, q75, q90 (`numpy.quantile`, линейная интерполяция), округление до 0,001.
    Совпадение соседних квантилей (после округления) — стоп: клетки не строятся, решает координатор (число клеток m меняется)."""
    w = num["width"][num["nvalid_rng"] == 1]
    q = {k: q3(float(np.quantile(w, k / 100.0))) for k in (10, 25, 75, 90)}
    if not (q[10] < q[25] < q[75] < q[90]):
        raise ValueError("совпадение квантилей ширины: %s — стоп (§13.31 п. 3.3)" % q)
    return q, int(len(w))


def fmt_curve(rows, title, p0):
    out = ["#### " + title, "", "| d, NATR | n | p_touch | p_up1 (от достигших) | p_touch/p0 |", "|---:|---:|---:|---:|---:|"]
    for k, d, n, nt, nu in rows:
        if n < 1:
            out.append("| %.2f | 0 | — | — | — |" % d)
            continue
        mark = "" if n >= NMIN else " (n<30)"
        out.append("| %.2f | %d%s | %.3f | %.3f | %.2f |" % (d, n, mark, nt / n, nu / n, nt / n / p0 if p0 else float("nan")))
    out.append("")
    return out


def by_month(num, kind):
    """p_touch(d) по месяцам для отчёта (описательно, правило не использует)."""
    out = ["| месяц | n сигналов | p0 | " + " | ".join("%.2f" % (STEP * k) for k in (2, 4, 6, 8, 12, 16, 20)) + " |", "|---|---:|---:|" + "---:|" * 7]
    for m in sorted(set(num["mon"].tolist())):
        sel = num["mon"] == m
        sub = {k: v[sel] for k, v in num.items()}
        rows, p0, bn = curve(sub, kind)
        cells = []
        for k in (2, 4, 6, 8, 12, 16, 20):
            n, nt = rows[k][2], rows[k][3]
            cells.append("%.2f (%d)" % (nt / n, n) if n >= NMIN else "— (%d)" % n)
        out.append("| %s | %d | %.3f | %s |" % (rf.MONTH_NAMES[m], bn, p0, " | ".join(cells)))
    out.append("")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--side", required=True, choices=list(SIDES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    num = load(a.root, a.variant, a.side)
    n_all = len(num["touch"])
    lvl, p0l, _ = curve(num, "lvl")
    edge, p0e, _ = curve(num, "edge")
    rl = rule_levels(lvl, p0l)
    rm = rule_margin(edge, p0e)
    wq, nw = rule_width(num)
    res = dict(side=a.side, variant=a.variant, n_signals=n_all, n_with_range=nw, p0_level=p0l, p0_edge=p0e, levels=rl, margin=rm, width_q=wq)
    lines = ["# TK-620 шаг 0 — %s %s" % (a.side, a.variant), "",
             "Сигналов (зачётные сутки): %d; с проторговкой W: %d. p0 (касание за горизонт) — «уровень» %.3f, «край» %.3f." % (n_all, nw, p0l, p0e), ""]
    lines += fmt_curve(lvl, "Кривая «уровень» (d ниже цены уровня)", p0l)
    lines += by_month(num, "lvl")
    lines += fmt_curve(edge, "Кривая «край проторговки» (d ниже дальнего края)", p0e)
    lines += by_month(num, "edge")
    lines += ["#### Правило §13.31 п. 3 (механически)", "",
              "- nx/sl: X_h = %s, X_q = %s → сетка %s %s" % (rl["xh"], rl["xq"], rl["grid"], rl["note"]),
              "- cm: M_h = %s, M_q = %s → значения %s %s" % (rm["mh"], rm["mq"], rm["grid"], rm["note"]),
              "- ширина проторговки в NATR (n=%d): q10 %.3f, q25 %.3f, q75 %.3f, q90 %.3f" % (nw, wq[10], wq[25], wq[75], wq[90]), ""]
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    print("ok", a.side, a.variant, n_all)


if __name__ == "__main__":
    main()
