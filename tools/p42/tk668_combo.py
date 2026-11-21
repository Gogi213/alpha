#!/usr/bin/env python3
"""П-42 §13.68 (TK-668): E4 / D шорт вместе с `S_base` — описательно, на ГОТОВЫХ сделках (движок не запускается, клетки не считаются, В-213).
Просьба координатора 21.11 (нужно для решения о заморозке вперёд): пересечение сделок (та же монета; интервалы [вход, выход) пересекаются или входы в пределах ± 5 мин),
сумма двух рядов (простая сумма суточных P&L, портфельные лимиты не моделируются — как «база + E8»), $ на сделку, Sharpe, макс. просадка $, суток до нового максимума (KPI В-120),
депозит по методу TK-664 (`tk664_funding_capital.sweep`: D1 = 250 $ × макс. одновременных позиций, D2 = мин. депозит с реализованным P&L), по месяцам D / шорт против `S_base`.
Запуск на srv: `PYTHONUTF8=1 /opt/venv/bin/python tk668_combo.py` (из /data/p42-tk668/code) → res/ds-sbase.json, res/ds-sbase.md. Вердикта нет."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk664_funding_capital as t64  # noqa: E402  (sweep, MARGIN, D0_MS, END_MS)
import tk668_e4 as e4  # noqa: E402
import lib  # noqa: E402

ROOT = e4.ROOT
NEAR_MS = 5 * 60_000
MONTHS = st.MONTH_NAMES


def f(x, nd=1):
    return ("%+." + str(nd) + "f") % x


def load_lines():
    u = r5.Uni("h775")
    base = p8.load_cell(u, "S", {w.name: (u.base(w.name, "S"), "S") for w in st.TRADE_WINDOWS})
    ds = p8.load_cell(u, "S", {w.name: (os.path.join(e4.ROOT, "e4", "cells", w.name, e4.vid("S", 1)), "S") for w in st.TRADE_WINDOWS})
    return base[0], ds[0]


def arrays(ser):
    return dict(sym=np.array([str(x) for x in ser.sym_name], dtype=object), t_in=np.asarray(ser.t_in, np.int64), t_out=np.asarray(ser.t_out, np.int64),
                day=np.asarray(ser.day, np.int64), pnl=np.asarray(ser.val, float))


def overlap(a, b):
    """Для каждой сделки `a`: есть ли у `b` сделка той же монеты с пересечением интервалов [вход, выход) или входом в пределах ± 5 мин. Возвращает три булевых вектора (пересечение, вход ± 5 мин, любое) и тот же вход (sym, t_in)."""
    by = {}
    for j in range(len(b["sym"])):
        by.setdefault(b["sym"][j], []).append(j)
    ov, nr, same = np.zeros(len(a["sym"]), bool), np.zeros(len(a["sym"]), bool), np.zeros(len(a["sym"]), bool)
    for i in range(len(a["sym"])):
        for j in by.get(a["sym"][i], ()):
            if a["t_in"][i] < b["t_out"][j] and b["t_in"][j] < a["t_out"][i]:
                ov[i] = True
            if abs(int(a["t_in"][i]) - int(b["t_in"][j])) <= NEAR_MS:
                nr[i] = True
            if a["t_in"][i] == b["t_in"][j]:
                same[i] = True
    return ov, nr, ov | nr, same


def summary(day, val):
    r = r34.slice_plus(day, val)
    Sd = np.bincount(day, weights=val, minlength=st.NDAYS)
    cum = np.concatenate([[0.0], np.cumsum(Sd)])
    dd = float((np.maximum.accumulate(cum) - cum).max())
    return dict(n=int(r["n"]), sum=float(r["sum"]), mean=float(r["mean"]), sharpe=float(r["sharpe"]), months=[(int(m[0]), float(m[1])) for m in r["months"]], sharpe_months=r["sharpe_months"],
                maxdd=dd, maxdd_pct_margin=dd / t64.MARGIN * 100, kpi=lib.drawdown_kpi(Sd), daily=Sd)


def deposit(t_in, t_out, pnl):
    s = t64.sweep(t_in, t_out, pnl)
    return dict(max_pos=s["max"], d1=s["max"] * t64.MARGIN, d2=s["d2"], mean_pos=s["mean"])


def main():
    base, ds = load_lines()
    A, B = arrays(base), arrays(ds)                        # A — S_base, B — D / шорт
    assert len(A["day"]) == 319 and len(B["day"]) == 1237 and abs(A["pnl"].sum() - 223.1) < 0.1 and abs(B["pnl"].sum() - 725.5) < 0.1, "сверка с отчётом (319 / +223,1; 1 237 / +725,5)"
    ov_b, nr_b, any_b, same_b = overlap(B, A)              # сделки D / шорт, у которых есть пара в S_base
    ov_a, nr_a, any_a, same_a = overlap(A, B)              # сделки S_base, у которых есть пара в D / шорт
    comb = {k: np.concatenate([A[k], B[k]]) for k in A}
    S = {"S_base": summary(A["day"], A["pnl"]), "D/S": summary(B["day"], B["pnl"]), "S_base + D/S": summary(comb["day"], comb["pnl"])}
    for nm, x in (("S_base", A), ("D/S", B), ("S_base + D/S", comb)):
        S[nm]["deposit"] = deposit(x["t_in"], x["t_out"], x["pnl"])
    corr = float(np.corrcoef(S["S_base"]["daily"], S["D/S"]["daily"])[0, 1])
    split = {}
    for nm, m in (("D/S с парой в S_base (пересечение или ± 5 мин)", any_b), ("D/S без пары в S_base", ~any_b)):
        p = B["pnl"][m]
        split[nm] = dict(n=int(m.sum()), sum=float(p.sum()), mean=float(p.mean()) if len(p) else float("nan"))
    ov = dict(ds_total=len(B["day"]), ds_overlap=int(ov_b.sum()), ds_near5=int(nr_b.sum()), ds_any=int(any_b.sum()), ds_same_entry=int(same_b.sum()),
              base_total=len(A["day"]), base_overlap=int(ov_a.sum()), base_near5=int(nr_a.sum()), base_any=int(any_a.sum()), base_same_entry=int(same_a.sum()),
              split=split, daily_corr=corr, coins_ds=len(set(B["sym"])), coins_base=len(set(A["sym"])), coins_both=len(set(B["sym"]) & set(A["sym"])))
    out = dict(overlap=ov, lines={k: {kk: vv for kk, vv in v.items() if kk != "daily"} for k, v in S.items()})
    json.dump(out, open(os.path.join(ROOT, "res", "ds-sbase.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)

    L = ["| ряд | n | Σ $ | $/сделку | Sharpe | макс. просадка $ (% маржи 250 $) | суток до нового максимума: дольше всего / p90 | D1 $ (макс. позиций) | D2 $ | Σ / D1 |", "|---|---:|---:|---:|---:|---|---|---:|---:|---:|"]
    for nm, r in S.items():
        d = r["deposit"]
        L.append("| %s | %d | %s | %s | %.2f | %.1f (%.1f %%) | %d / %.1f | %.0f (%d) | %.0f | %.2f |" % (
            nm, r["n"], f(r["sum"]), f(r["mean"], 3), r["sharpe"], r["maxdd"], r["maxdd_pct_margin"], r["kpi"]["longest"], r["kpi"]["p90"], d["d1"], d["max_pos"], d["d2"], r["sum"] / d["d1"]))
    L += ["", "| месяц | S_base: сделок / Σ $ | D / шорт: сделок / Σ $ | вместе: сделок / Σ $ | Sharpe месяца: S_base / D / шорт / вместе |", "|---|---|---|---|---|"]
    for k, mn in enumerate(MONTHS):
        c = [S[x]["months"][k] for x in ("S_base", "D/S", "S_base + D/S")]
        sm = [S[x]["sharpe_months"][k] for x in ("S_base", "D/S", "S_base + D/S")]
        L.append("| %s | %d / %s | %d / %s | %d / %s | %.2f / %.2f / %.2f |" % (mn, c[0][0], f(c[0][1], 0), c[1][0], f(c[1][1], 0), c[2][0], f(c[2][1], 0), *sm))
    open(os.path.join(ROOT, "res", "ds-sbase.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(json.dumps(ov, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
