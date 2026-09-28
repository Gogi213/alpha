#!/usr/bin/env python3
"""П-08 — чтение августа/сентября (§6, §9 п. 3, §10.2 с поправкой В-146, §12 п. 18; TK-013).

Клетки — итоговые из `docs/findings/p08-coverage-2026-09-28.json` (`tmp-p08/cov/p08-coverage.json` на деке), закрытия —
`tmp-p08/run/h9-b-<клетка>/ps-closes.json` (`p08-run.py cells`). Базы: B1 — `p08-keepall` (Г-85б как есть, без потолка),
B2 — `p08-keepall-b2` (потолок 3) для клеток Г-126. Для клетки и месяца: n, $, доля часов с ожиданием перехая > 5 сут,
макс. ожидание (ч, «≥» при цензуре), состояние §10 П-07 (исключения суток и монет — описание), охват (доля сделок
базы), Δ$ к базе парно по суткам закрытия (блочный бутстреп `p07-read.block_boot`) и p для Холма (m = 12 на месяц,
справочно). Отбор на июль (В-146): $ авг ≥ 0 и $ сен ≥ 0 — и только это. Авг/сен — развитие, не проверка.
Функции чтения — импортом из `tmp-p07/p07-read.py` и `p07-tk009-read.py` (одно определение).

    python3 bin/p08-read.py > tmp-p08/p08-read.log      # итог — tmp-p08/p08-read.json
"""
import importlib.util
import json
import os

A = os.path.expanduser("~/alpha")
RUN = os.path.join(A, "tmp-p08", "run")
COV = os.path.join(A, "tmp-p08", "cov", "p08-coverage.json")
HERE = os.path.dirname(os.path.abspath(__file__))
PK = (("aug", "август"), ("sep", "сентябрь"))
BASE_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = load(os.path.join(A, "tmp-p07", "p07-read.py"), "p07read")
kn = load(R.KN_PATH, "kn")
T9 = load(next(p for p in (os.path.join(HERE, "p07-tk009-read.py"), os.path.join(A, "tmp-p07", "p07-tk009-read.py"))
               if os.path.exists(p)), "t9")


def closes_for(name, cap):
    co = json.load(open(os.path.join(RUN, f"h9-b-{name}", "ps-closes.json"), encoding="utf-8"))
    return {pk: [tuple(x) for x in co.get("п07b", {}).get(ru, {}).get(str(cap), [])] for pk, ru in PK}


def read_cell(name, cap):
    cl = closes_for(name, cap)
    all_c = sorted(cl["aug"] + cl["sep"])
    dirs = {t: os.path.join(RUN, f"h9-b-{name}", t) for t in ("aug", "hist", "rec")}
    kb = R.kpi_block(kn, all_c, R.symbol_map(dirs, BASE_FORM))
    roll = kn.rolling_kpi(all_c, h_days=5)
    return {"cap": cap, "n": {pk: len(cl[pk]) for pk, _ in PK},
            "usd": {pk: round(sum(p for _, p in cl[pk]), 2) for pk, _ in PK},
            "frac": kb["frac_gt_5d"], "state": kb["verdict"], "states": kb["states"],
            "max_gap_h": {pk: (round(roll[pk]["max"]["max"], 1) if roll[pk]["max"]["max"] is not None else None,
                               roll[pk]["max"]["max_censored"]) for pk, _ in PK},
            "_closes": cl}


def main():
    cov = json.load(open(COV, encoding="utf-8"))
    m = cov["m_family"]
    bases = {"B1": read_cell("p08-keepall", 0), "B2": read_cell("p08-keepall-b2", 3)}
    rows = {}
    for sub, cells in cov["final"].items():
        for c in cells:
            bk = "B2" if sub == "g126" else "B1"
            r = read_cell(c["name"], 3 if bk == "B2" else 0)
            b = bases[bk]
            r["base"] = bk
            r["coverage"] = {pk: round(r["n"][pk] / b["n"][pk], 3) if b["n"][pk] else None for pk, _ in PK}
            d = R.diff_vs_base(r["_closes"], b["_closes"])
            r["delta_usd"] = {pk: {"est": d[pk]["est"], "ci95": d[pk]["ci95"]} for pk, _ in PK}
            r["p"] = {}
            for pk, days in (("aug", R.AUG_DAYS), ("sep", R.SEP_DAYS)):
                uc, _ = R.daily_series(r["_closes"][pk], days)
                ub, _ = R.daily_series(b["_closes"][pk], days)
                r["p"][pk] = round(T9.boot_p([x - y for x, y in zip(uc, ub)]), 5)
            r["frac_le_base"] = {pk: (r["frac"][pk] is not None and b["frac"][pk] is not None
                                      and r["frac"][pk] <= b["frac"][pk]) for pk, _ in PK}
            r["to_july"] = r["usd"]["aug"] >= 0 and r["usd"]["sep"] >= 0  # В-146
            rows[c["name"]] = r
    for pk, _ in PK:  # Холм по Δ$, m = m_family (справочно, §10.2)
        ps = sorted((r["p"][pk], lab) for lab, r in rows.items())
        stop = False
        for i, (p, lab) in enumerate(ps):
            ok = (not stop) and p <= 0.05 / (m - i)
            stop = stop or not ok
            rows[lab].setdefault("holm_sig", {})[pk] = ok
    july = [lab for lab, r in rows.items() if r["to_july"]]
    strip = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    out = {"protocol": "docs/research/P-08-new-code-7.md §6, §10.2 (В-146), §12 п. 18", "m_holm": m,
           "note": "август/сентябрь — развитие, не проверка; Г-36 найдена на этих данных; Г-78 мера 15 мин ≠ мере отбора",
           "bases": {k: strip(v) for k, v in bases.items()}, "cells": {k: strip(v) for k, v in rows.items()},
           "to_july": july}
    json.dump(out, open(os.path.join(A, "tmp-p08", "p08-read.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    for lab, r in list(bases.items()) + list(rows.items()):
        print(lab, r.get("base", ""), r["n"], r["usd"], r["frac"], r["max_gap_h"], r["state"],
              r.get("coverage", ""), r.get("delta_usd", ""), r.get("p", ""), r.get("holm_sig", ""),
              "→ июль" if r.get("to_july") else "")
    print("на июль (В-146):", july)
    print("DONE p08-read.json")


if __name__ == "__main__":
    main()
