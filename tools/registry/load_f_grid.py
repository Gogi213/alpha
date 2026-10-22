#!/usr/bin/env python3
"""TK-200 пакет F: сетки closure_mech (9 мес.) -> docs/registry/family_points.jsonl (семья + оси + месяц, БЕЗ канон. хеша, вне хеш-счёта; design-v227 §2.4)
+ таблица В-224 (семья — точек — месяцев — строк). Без пересчёта (В-213): читает готовые csv. load_f_grid.py <каталог grid> --run ID --out family_points.jsonl [--cover cover.md]"""
import argparse, collections, csv, glob, json, os, re

HYP = {"g111_impulse_target": "Г-111", "g118_macd_trail": "Г-118", "g124_profit_throttle": "Г-124", "g126_approach_brake": "Г-126",
       "g130_stop_round": "Г-130", "g144_beta_hedge": "Г-144", "g148_leverage": "Г-148", "g18_fee_share": "Г-18", "g19_markout_rank": "Г-19",
       "g26_vol_family": "Г-26", "g50_tf_boundary": "Г-50", "g80_depth_excursion": "Г-80", "p07_h14_approach_no": "Г-138", "p07_h9_pause": "Г-103",
       "p07_h9r_fail": "Г-38", "p07_h10_cap": "Г-20"}
H14_RULE = {"k": "Г-142", "n": "Г-143", "e": "Г-138"}  # оси H14 (Судья 12.10 11:40): k<K> первые K, n<k_prior+1> пережила, e<k> ровно k-й

IDS = {"mechanic", "month", "period", "cap", "point", "variant"}
ap = argparse.ArgumentParser(); ap.add_argument("grid"); ap.add_argument("--run", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--cover")
ap.add_argument("--families", help="через запятую: считать только эти семьи")
ap.add_argument("--keep-from", help="jsonl: перенести строки других семей (дельта, В-213)"); ns = ap.parse_args()


def hyp_of(fam, point):
    if fam == "p07_h9r_fail":  # s1k<k_fail> -> Г-38, s3h<h> -> Г-145
        return "Г-145" if point.split("=")[-1].startswith("s3") else "Г-38"
    if fam == "p07_h14_approach_no":
        return H14_RULE[point.split("=")[-1][0]]
    return HYP[fam]


def num(v):
    try:
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
    except (TypeError, ValueError):
        return v


rows = 0; cover = collections.defaultdict(lambda: [set(), set(), 0])
fams = set(ns.families.split(",")) if ns.families else set(HYP)
with open(ns.out, "w", encoding="utf-8", newline="\n") as fo:
    if ns.keep_from:
        for ln in open(ns.keep_from, encoding="utf-8"):
            d = json.loads(ln)
            if d["family"] not in fams:
                fo.write(ln if ln.endswith("\n") else ln + "\n"); rows += 1
                c = cover[(d["hyp_id"], d["family"])]; c[0].add(d["key"].split("|")[0]); c[1].add(d["month"]); c[2] += 1
    for fam in HYP:
        if fam not in fams:
            continue
        for f in sorted(glob.glob(os.path.join(ns.grid, fam + "*-2026-??.csv"))):
            for r in csv.DictReader(open(f, encoding="utf-8")):
                month = r.get("month") or r.get("period")
                key = r["point"] + (f"|{r['rank']}" if "rank" in r else "") + (f"|{r['bin']}" if "bin" in r else "") + (f"|{r['reason']}" if "reason" in r else "")
                axes = dict(p.split("=", 1) for p in r["point"].split(";") if "=" in p) or {"point": r["point"]}
                ext = {k: num(v) for k, v in r.items() if k not in IDS and v != ""}
                d = {"run_id": ns.run, "hyp_id": hyp_of(fam, r["point"]), "family": fam, "axes": axes, "month": month, "cap": r.get("cap"), "key": key, "ext": ext}
                fo.write(json.dumps(d, ensure_ascii=False) + "\n"); rows += 1
                c = cover[(d["hyp_id"], fam)]; c[0].add(r["point"]); c[1].add(month); c[2] += 1
print("строк", rows)
if ns.cover:
    with open(ns.cover, "w", encoding="utf-8", newline="\n") as fc:
        fc.write("гипотеза | семья | точек | месяцев из 9 | строк\n---|---|---|---|---\n")
        for (h, fam), (p, m, n) in sorted(cover.items()):
            fc.write(f"{h} | {fam} | {len(p)} | {len(m)} | {n}\n")
