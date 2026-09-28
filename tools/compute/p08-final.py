#!/usr/bin/env python3
"""П-08 — добор к итоговому отчёту (условия Судьи 1 и 2, reviews/P-08-2026-09-28-augsep.md; TK-013).

(1) Устойчивость знака $ (§9 п. 3): по клетке и месяцу — $ без каждых одних суток (мин./макс., число смен знака) и без
монеты с наибольшим вкладом (наибольший плюс; и наибольший по модулю). Закрытия — те же, что в чтении: авг/сен
`tmp-p08/run/h9-b-<клетка>/ps-closes.json`, июль `tmp-p08/jul/<клетка>/ps-closes.json`; монета — `symbol_map`
busy-replay выходов клетки (одно определение, `p07-read.py`).
(2) Отбор (§8): дневной Шарп $ клетки и Δ$ к базе по месяцам; ожидаемый максимум шума и DSR —
`selection_bias.py` с N = 163 (165 − 2 слияния клеток §12 п. 18).

    python3 bin/p08-final.py > tmp-p08/p08-final.log      # итог — tmp-p08/p08-final.json
"""
import importlib.util
import json
import math
import os

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
N_TRIALS = 163


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


try:
    import numpy  # noqa: F401
except ImportError:  # на деке numpy нет; expected_max_sharpe/deflated_sharpe его не используют
    import sys
    import types
    sys.modules["numpy"] = types.SimpleNamespace(ndarray=object)

JR = load(os.path.join(HERE, "p07-jul-read.py"), "jr")
PR = JR.load_mod("p07-read.py", "p07read")
SB = load(next(p for p in (os.path.join(HERE, "selection_bias.py"),
                           os.path.join(A, ".claude/skills/alpha-research/scripts/selection_bias.py"))
               if os.path.exists(p)), "sb")
DAYS = {"aug": PR.AUG_DAYS, "sep": PR.SEP_DAYS, "jul": JR.JUL_DAYS}


def closes(name, cap):
    """{месяц: [(t_ms, $)]}, {t_ms: монета}"""
    out, sym = {}, {}
    run = os.path.join(A, "tmp-p08", "run", f"h9-b-{name}")
    co = json.load(open(os.path.join(run, "ps-closes.json"), encoding="utf-8"))
    for pk, ru in (("aug", "август"), ("sep", "сентябрь")):
        out[pk] = [tuple(x) for x in co.get("п07b", {}).get(ru, {}).get(str(cap), [])]
    sym.update(PR.symbol_map({t: os.path.join(run, t) for t in ("aug", "hist", "rec")}, FORM))
    jn = {"p08-keepall": "B1", "p08-keepall-b2": "B2"}.get(name, name)
    jd = os.path.join(A, "tmp-p08", "jul", jn)
    cj = json.load(open(os.path.join(jd, "ps-closes.json"), encoding="utf-8"))
    out["jul"] = [tuple(x) for x in cj.get("cell", {}).get("июль", {}).get(str(cap), [])]
    sym.update(PR.symbol_map({"jul": os.path.join(jd, "jul")}, FORM))
    return out, sym


def stability(cl, sym, days):
    tot = sum(p for _, p in cl)
    daily, _ = PR.daily_series(cl, days)
    wo_day = [tot - x for x in daily]
    by = {}
    for t, p in cl:
        s = sym.get(t, "?")
        by[s] = by.get(s, 0.0) + p
    top_pos = max(by.items(), key=lambda kv: kv[1]) if by else ("-", 0.0)
    top_abs = max(by.items(), key=lambda kv: abs(kv[1])) if by else ("-", 0.0)
    sign = (tot > 0) - (tot < 0)
    return {"usd": round(tot, 2), "wo_day_min": round(min(wo_day), 2), "wo_day_max": round(max(wo_day), 2),
            "wo_day_sign_flips": sum(1 for x in wo_day if ((x > 0) - (x < 0)) != sign),
            "wo_top_coin": [top_pos[0], round(tot - top_pos[1], 2)],
            "wo_abs_coin": [top_abs[0], round(tot - top_abs[1], 2)],
            "unmapped": sum(1 for t, _ in cl if t not in sym)}


def sharpe(x):
    n = len(x)
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    s = mu / sd if sd > 0 else 0.0
    se = math.sqrt((1 + 0.5 * s * s) / n)
    return s, se


def main():
    cov = json.load(open(os.path.join(A, "tmp-p08", "cov", "p08-coverage.json"), encoding="utf-8"))
    bases = {"B1": closes("p08-keepall", 0), "B2": closes("p08-keepall-b2", 3)}
    cells = [("B1", None, bases["B1"]), ("B2", None, bases["B2"])]
    for sub, cs in cov["final"].items():
        for c in cs:
            bk = "B2" if sub == "g126" else "B1"
            cells.append((c["name"], bk, closes(c["name"], 3 if bk == "B2" else 0)))
    res = {}
    for name, bk, (cl, sym) in cells:
        r = {}
        for pk, days in DAYS.items():
            st = stability(cl[pk], sym, days)
            uc, _ = PR.daily_series(cl[pk], days)
            s, se = sharpe(uc)
            st["sharpe_d"] = round(s, 3)
            st["se"] = round(se, 3)
            if bk:
                ub, _ = PR.daily_series(bases[bk][0][pk], days)
                sd, sed = sharpe([x - y for x, y in zip(uc, ub)])
                st["sharpe_delta_d"] = round(sd, 3)
                st["e_max_noise"] = round(SB.expected_max_sharpe(sed, N_TRIALS), 3)
                st["dsr_delta"] = round(SB.deflated_sharpe(sd, sed, N_TRIALS), 4)
            r[pk] = st
        res[name] = r
        print(name, {pk: (v["usd"], v["wo_day_min"], v["wo_day_sign_flips"], v["wo_top_coin"], v.get("dsr_delta"))
                     for pk, v in r.items()})
    json.dump({"protocol": "docs/research/P-08-new-code-7.md §8, §9 п. 3; условия Судьи 1–2 (P-08-2026-09-28-augsep)",
               "n_trials": N_TRIALS, "cells": res},
              open(os.path.join(A, "tmp-p08", "p08-final.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("DONE p08-final.json")


if __name__ == "__main__":
    main()
