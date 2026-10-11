#!/usr/bin/env python3
"""TK-009 — чтение пачки по П-07 поправке 6 п. 4 (Судья `48013f7`, `1cda710`; В-144/В-146), база сравнения — Г-85б K ≤ 1.

Клетки (все уже с фильтром K ≤ 1, `p07-h9r-h14.py`): 11 новых сетки (`t9-new.txt`), 35 прежних Г-85б (`t9-old.txt`),
5 K ≤ 1 × H1/H10 (`k1f25/50/75`, `k1cap3/5` на `p07b-base`). База — `h9r-p07b-base-k1` (тождество с TK-004).
Для каждой клетки и месяца: n, $, доля часов с ожиданием перехая > 5 сут (`rolling_kpi`), макс. ожидание (ч,
цензура — «≥»), состояние §10 (`p07-read.verdict_kpi_fixed`: исключения суток и монет), Δ$ к базе парно по суткам
закрытия (блочный бутстреп `p07-read.block_boot`) и p для Холма (m = 51 на месяц); Б1 (точка: авг ≤ база − 0,10,
сен ≤ база), Б2 парно (для каждых суток d: авг клетки без d ≤ авг базы без d − 0,10, сен клетки без d ≤ сен базы
без d), Б3 (верх Δ$ ≥ 0 в обоих месяцах), В-144 ($ авг ≥ 0 и сен ≥ 0). Кандидат на июль — только новая клетка
сетки при Б1–Б3 и В-144, не больше 3 (по доле августа, ничья — по сентябрю). Всё — «найдено поиском на авг–сен».

    python3 p07-tk009-read.py > t9-read.log      # в ~/alpha/tmp-p07; итог — t9-read.json
"""
import datetime as dt
import importlib.util
import json
import math
import os
import random

HERE = os.path.expanduser("~/alpha/tmp-p07")
M_HOLM = 51
PK = (("aug", "август"), ("sep", "сентябрь"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = load(os.path.join(HERE, "p07-read.py"), "p07read")
kn = load(R.KN_PATH, "kn")
BASE_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"


def cell_list():
    out = [("база K≤1", "h9r-p07b-base-k1", BASE_FORM, 0, "база")]
    for fn, kind in (("t9-new.txt", "сетка"), ("t9-old.txt", "прежняя ось")):
        for line in open(os.path.join(HERE, fn), encoding="utf-8"):
            if line.strip():
                d, form, _set = line.split()
                out.append((d, f"h9r-{d}-k1", form, 0, kind))
    for c, cap in (("k1f25", 0), ("k1f50", 0), ("k1f75", 0), ("k1cap3", 3), ("k1cap5", 5)):
        out.append((c, f"h9r-p07b-base-{c}", BASE_FORM, cap, "прежняя ось"))
    return out


def closes_for(name, cap):
    co = json.load(open(os.path.join(HERE, f"h9-b-{name}", "ps-closes.json"), encoding="utf-8"))
    return {pk: [tuple(x) for x in co.get("п07b", {}).get(ru, {}).get(str(cap), [])] for pk, ru in PK}


def day_ms(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def fracs_without_days(closes_all):
    ev = sorted(closes_all)
    out = {}
    for d in sorted({day_ms(t) for t, _ in ev}):
        out[d] = kn._frac_gt_h_pair([(t, p) for t, p in ev if day_ms(t) != d], 5)
    return out


def boot_p(units, reps=20000, seed=20260927):
    """Двусторонний p суммы по суткам ≠ 0 тем же блочным бутстрепом, что `block_boot` (центрированный)."""
    n = len(units)
    if n == 0:
        return 1.0
    b = max(1, math.ceil(n ** (1 / 3)))
    mean = sum(units) / n
    c = [u - mean for u in units]
    rng = random.Random(seed)
    k = math.ceil(n / b)
    obs = abs(sum(units))
    hit = 0
    for _ in range(reps):
        s = []
        for _ in range(k):
            i = rng.randrange(n)
            s.extend(c[(i + j) % n] for j in range(b))
        if abs(sum(s[:n])) >= obs:
            hit += 1
    return (hit + 1) / (reps + 1)


def main():
    rows = {}
    base = None
    for label, name, form, cap, kind in cell_list():
        path = os.path.join(HERE, f"h9-b-{name}", "ps-closes.json")
        if not os.path.exists(path):
            print(f"нет {path}")
            continue
        cl = closes_for(name, cap)
        all_c = sorted(cl["aug"] + cl["sep"])
        dirs = {t: os.path.join(HERE, f"h9-b-{name}", t) for t in ("aug", "hist", "rec")}
        sym = R.symbol_map(dirs, form)
        kb = R.kpi_block(kn, all_c, sym)
        roll = kn.rolling_kpi(all_c, h_days=5)
        r = {"kind": kind, "n": {pk: len(cl[pk]) for pk, _ in PK},
             "usd": {pk: round(sum(p for _, p in cl[pk]), 2) for pk, _ in PK},
             "frac": kb["frac_gt_5d"], "state": kb["verdict"], "states": kb["states"],
             "max_gap_h": {pk: (round(roll[pk]["max"]["max"], 1) if roll[pk]["max"]["max"] is not None else None,
                                roll[pk]["max"]["max_censored"]) for pk, _ in PK},
             "_closes": cl, "_wo": fracs_without_days(all_c)}
        rows[label] = r
        if kind == "база":
            base = r
    assert base is not None, "нет базы K≤1"
    for label, r in rows.items():
        if r["kind"] == "база":
            continue
        d = R.diff_vs_base(r["_closes"], base["_closes"])
        r["delta_usd"] = {pk: {"est": d[pk]["est"], "ci95": d[pk]["ci95"]} for pk, _ in PK}
        r["p"] = {}
        for pk, days in (("aug", R.AUG_DAYS), ("sep", R.SEP_DAYS)):
            uc, _ = R.daily_series(r["_closes"][pk], days)
            ub, _ = R.daily_series(base["_closes"][pk], days)
            r["p"][pk] = round(boot_p([a - b for a, b in zip(uc, ub)]), 5)
        fa, fs, ba, bs = r["frac"]["aug"], r["frac"]["sep"], base["frac"]["aug"], base["frac"]["sep"]
        r["B1"] = fa is not None and fs is not None and fa <= ba - 0.10 and fs <= bs
        b2 = True
        for day in set(r["_wo"]) | set(base["_wo"]):
            ca, cs = r["_wo"].get(day, (fa, fs))
            xa, xs = base["_wo"].get(day, (ba, bs))
            if None in (ca, cs, xa, xs) or ca > xa - 0.10 or cs > xs:
                b2 = False
                break
        r["B2_paired"] = b2
        r["B3"] = all(r["delta_usd"][pk]["ci95"][1] >= 0 for pk, _ in PK)
        r["V144"] = r["usd"]["aug"] >= 0 and r["usd"]["sep"] >= 0
        r["candidate_rule"] = r["kind"] == "сетка" and r["B1"] and r["B2_paired"] and r["B3"] and r["V144"]
    # Холм по деньгам: m = 51 на месяц (поправка 6 п. 4, 1cda710)
    for pk, _ in PK:
        ps = sorted((r["p"][pk], lab) for lab, r in rows.items() if "p" in r)
        stop = False
        for i, (p, lab) in enumerate(ps):
            ok = (not stop) and p <= 0.05 / (M_HOLM - i)
            stop = stop or not ok
            rows[lab].setdefault("holm_sig", {})[pk] = ok
    cands = sorted((lab for lab, r in rows.items() if r.get("candidate_rule")),
                   key=lambda lab: (rows[lab]["frac"]["aug"], rows[lab]["frac"]["sep"]))[:3]
    out = {"base": "Г-85б K≤1 (h9r-p07b-base-k1)", "m_holm": M_HOLM, "candidates_july": cands,
           "note": "найдено поиском на августе–сентябре (П-07 поправка 6); прежние оси и K≤1×H1/H10 — описание",
           "cells": {lab: {k: v for k, v in r.items() if not k.startswith("_")} for lab, r in rows.items()}}
    json.dump(out, open(os.path.join(HERE, "t9-read.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for lab, r in rows.items():
        print(lab, r["kind"], r["n"], r["usd"], r["frac"], r["state"],
              {k: r.get(k) for k in ("B1", "B2_paired", "B3", "V144", "candidate_rule")})
    print("кандидаты на июль:", cands)
    print("DONE t9-read.json")


if __name__ == "__main__":
    main()
