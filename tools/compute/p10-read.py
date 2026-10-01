#!/usr/bin/env python3
"""П-10 — чтение августа/сентября (§10.1–10.2, правки Судьи 1–6 §14, TK-024). Клетки — итоговые из JSON охвата
(`docs/findings/p10-coverage-2026-10-02.json`, на деке `tmp-p10/cov/p10-coverage.json`), закрытия — `tmp-p10/run/h9-b-<клетка>[-b2]/`
(`p10-run.py cells`). Базы: B1 = `p10-keepall` (потолок 0), B2 = `p10-keepall-b2` (потолок 3); для Г-89 справочно
`p10-keepall-market` (рынок без фильтра). Для клетки и месяца: n, $, суток с сделкой, доля часов KPI (> 5 сут), макс. ожидание,
состояние §10 П-07, охват (доля сделок B1), Δ$ к B1 парно по суткам (блочный бутстреп `p07-read.block_boot`), p, С2, пик
позиций, B2 (С1/$/С3 против B2) для правила объёма. Правило §10.2: С1 (доля ≤ B1 − 0,10 в обоих месяцах), $ ≥ 0, С3 (верх
95 % Δ$ ≥ 0), определена (§10.1: ≥ 10 сделок и ≥ 10 суток); объём (Судья п. 5): сделок > 1,25 × B1 или пик позиций выше
B1 в месяце → ещё С1 и $ ≥ 0 на B2, иначе ярлык «на объёме, не отобрана». Семья проходит, если хотя бы одна её клетка.
Холм по семьям, m = `m_family` из JSON (T/U/V — не определены, p = 1).

    python3 bin/p10-read.py [--quiet] [--only a,b]  # итог — tmp-p10/p10-read.json
"""
import argparse
import importlib.util
import json
import os

A = os.path.expanduser("~/alpha")
RUN = os.path.join(A, "tmp-p10", "run")
COV = os.path.join(A, "tmp-p10", "cov", "p10-coverage.json")
HERE = os.path.dirname(os.path.abspath(__file__))
PK = (("aug", "август"), ("sep", "сентябрь"))
BASE_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
MKT_FORM = "market-pct2-tr1x1-14400-ttl1800"
C1_DELTA = 0.10          # §10.2 п. 1 (порог П-08 С1)
VOL_K = 1.25             # Судья п. 5, назначен разбором
MIN_N, MIN_DAYS, FEW_N = 10, 10, 30   # §10.1, п. 19


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = load(os.path.join(A, "tmp-p07", "p07-read.py"), "p07read")
kn = load(R.KN_PATH, "kn")
T9 = load(next(p for p in (os.path.join(HERE, "p07-tk009-read.py"), os.path.join(A, "tmp-p07", "p07-tk009-read.py"))
               if os.path.exists(p)), "t9")
EP = {"aug": "август", "sep": "сентябрь"}


def closes_for(dirname, cap):
    co = json.load(open(os.path.join(RUN, dirname, "ps-closes.json"), encoding="utf-8"))
    return {pk: [tuple(x) for x in co.get("п07b", {}).get(ru, {}).get(str(cap), [])] for pk, ru in PK}


def peaks(dirname, cap):
    ps = json.load(open(os.path.join(RUN, dirname, "ps.json"), encoding="utf-8"))
    out = {}
    for g in ps["grid"]:
        if g["max_pos"] == cap:
            for pk, ru in PK:
                if g["epoch"] == ru:
                    out[pk] = g["peak_n"]
    return out


def read_cell(dirname, cap, form):
    cl = closes_for(dirname, cap)
    all_c = sorted(cl["aug"] + cl["sep"])
    dirs = {t: os.path.join(RUN, dirname, t) for t in ("aug", "hist", "rec")}
    kb = R.kpi_block(kn, all_c, R.symbol_map(dirs, form))
    roll = kn.rolling_kpi(all_c, h_days=5)
    days = {}
    best_day = {}
    for pk, ds in (("aug", R.AUG_DAYS), ("sep", R.SEP_DAYS)):
        ser, _ = R.daily_series(cl[pk], ds)
        days[pk] = sum(1 for (t, _) in cl[pk]) and sum(1 for x in ser if x != 0)
        best_day[pk] = max(ser) if ser else 0.0
    usd = {pk: round(sum(p for _, p in cl[pk]), 2) for pk, _ in PK}
    return {"cap": cap, "n": {pk: len(cl[pk]) for pk, _ in PK}, "usd": usd, "days_with_trades": days,
            "frac": kb["frac_gt_5d"], "state": kb["verdict"],
            "max_gap_h": {pk: (round(roll[pk]["max"]["max"], 1) if roll[pk]["max"]["max"] is not None else None,
                               roll[pk]["max"]["max_censored"]) for pk, _ in PK},
            "peak_n": peaks(dirname, cap),
            "usd_wo_best_day": {pk: round(usd[pk] - best_day[pk], 2) for pk, _ in PK},
            "_closes": cl}


def vs_base(r, b):
    d = R.diff_vs_base(r["_closes"], b["_closes"])
    out = {"delta_usd": {pk: {"est": d[pk]["est"], "ci95": d[pk]["ci95"]} for pk, _ in PK}, "p": {}}
    for pk, days in (("aug", R.AUG_DAYS), ("sep", R.SEP_DAYS)):
        uc, _ = R.daily_series(r["_closes"][pk], days)
        ub, _ = R.daily_series(b["_closes"][pk], days)
        out["p"][pk] = round(T9.boot_p([x - y for x, y in zip(uc, ub)]), 5)
    return out


def c1(r, b):
    return {pk: (r["frac"][pk] is not None and b["frac"][pk] is not None and r["frac"][pk] <= b["frac"][pk] - C1_DELTA)
            for pk, _ in PK}


def verdict_cell(name, form, b1, b2):
    r = read_cell(f"h9-b-{name}", 0, form)
    defined = {pk: r["n"][pk] >= MIN_N and r["days_with_trades"][pk] >= MIN_DAYS for pk, _ in PK}
    r["defined"] = defined
    r["label"] = "мало сделок" if any(MIN_N <= r["n"][pk] < FEW_N for pk, _ in PK) else ""
    r["coverage"] = {pk: round(r["n"][pk] / b1["n"][pk], 3) if b1["n"][pk] else None for pk, _ in PK}
    r.update(vs_base(r, b1))
    for pk, _ in PK:
        if not defined[pk]:
            r["p"][pk] = 1.0
    r["c1"] = c1(r, b1)
    r["usd_ge0"] = {pk: r["usd"][pk] >= 0 for pk, _ in PK}
    r["c3"] = {pk: r["delta_usd"][pk]["ci95"][1] >= 0 for pk, _ in PK}
    r["c2_wo_best_day_ge0"] = {pk: r["usd_wo_best_day"][pk] >= 0 for pk, _ in PK}
    r["on_volume"] = {pk: (r["n"][pk] > VOL_K * b1["n"][pk]) or (r["peak_n"].get(pk, 0) > b1["peak_n"].get(pk, 0))
                      for pk, _ in PK}
    base_ok = all(defined.values()) and all(r["c1"].values()) and all(r["usd_ge0"].values()) and all(r["c3"].values())
    r["pass_b1"] = base_ok
    r["b2"] = None
    if any(r["on_volume"].values()):
        r2 = read_cell(f"h9-b-{name}-b2", 3, form)
        r["b2"] = {"n": r2["n"], "usd": r2["usd"], "frac": r2["frac"], "max_gap_h": r2["max_gap_h"],
                   "c1": c1(r2, b2), "usd_ge0": {pk: r2["usd"][pk] >= 0 for pk, _ in PK}}
        r["b2"]["ok"] = all(r["b2"]["c1"].values()) and all(r["b2"]["usd_ge0"].values())
    r["to_selection"] = base_ok and (r["b2"] is None or r["b2"]["ok"])
    if base_ok and not r["to_selection"]:
        r["label"] = (r["label"] + "; " if r["label"] else "") + "на объёме, не отобрана"
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="только статус, без чисел (проверка кода до чтения)")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=os.path.join(A, "tmp-p10", "p10-read.json"))
    a = ap.parse_args()
    cov = json.load(open(COV, encoding="utf-8"))
    m = cov["m_family"]
    only = set(filter(None, a.only.split(",")))
    b1 = read_cell("h9-b-p10-keepall", 0, BASE_FORM)
    b2 = read_cell("h9-b-p10-keepall-b2", 3, BASE_FORM)
    mk = read_cell("h9-b-p10-keepall-market", 0, MKT_FORM)
    rows, fam_of = {}, {}
    for fam, cells in cov["final"].items():
        for c in cells:
            if only and c["name"] not in only:
                continue
            form = MKT_FORM if fam == "G89" else BASE_FORM
            r = verdict_cell(c["name"], form, b1, b2)
            r["family"], r["охват_ярлык"] = fam, c.get("label", "")
            if fam == "G89":
                r["vs_market_all"] = vs_base(r, mk)
            rows[c["name"]] = r
            fam_of[c["name"]] = fam
    fams = {}
    for lab, r in rows.items():
        fams.setdefault(r["family"], []).append(lab)
    fam_rows = {}
    for f, labs in fams.items():
        fam_rows[f] = {"cells": labs, "to_selection": any(rows[x]["to_selection"] for x in labs),
                       "p": {pk: min(rows[x]["p"][pk] for x in labs) for pk, _ in PK}}
    for tag in cov.get("families_exitsim", []):  # T/U/V — ворота exit-sim не пройдены (§14 02:40): p = 1
        fam_rows[tag] = {"cells": [], "to_selection": False, "p": {"aug": 1.0, "sep": 1.0},
                         "note": "не определена: ворота exit-sim вне допуска, только описание"}
    for pk, _ in PK:  # Холм по семьям, m = m_family
        ps = sorted((v["p"][pk], f) for f, v in fam_rows.items())
        stop = False
        for i, (p, f) in enumerate(ps):
            ok = (not stop) and p <= 0.05 / (m - i)
            stop = stop or not ok
            fam_rows[f].setdefault("holm_sig", {})[pk] = ok
    strip = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    sel = [f for f, v in fam_rows.items() if v["to_selection"]]
    out = {"protocol": "docs/research/P-10-code-exists-batch.md §10.1–10.2, §14 п. 5", "m_holm": m,
           "note": "найдено на авг/сен — поиск, не проверка; семья проходит при хотя бы одной клетке; T/U/V — описание",
           "bases": {"B1": strip(b1), "B2": strip(b2), "market_all": strip(mk)},
           "cells": {k: strip(v) for k, v in rows.items()}, "families": fam_rows, "to_selection": sel}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.quiet:
        print(f"OK {len(rows)} клеток, {len(fam_rows)} семей; чисел не печатаю (--quiet)")
        return
    for lab, r in [("B1", b1), ("B2", b2)] + list(rows.items()):
        print(lab, r["n"], r["usd"], r["frac"], r["max_gap_h"], r["state"], r.get("coverage", ""),
              r.get("delta_usd", ""), r.get("p", ""), "отбор" if r.get("to_selection") else "", r.get("label", ""))
    print("в отбор на проверку вне выборки:", sel)
    print("DONE", a.out)


if __name__ == "__main__":
    main()
