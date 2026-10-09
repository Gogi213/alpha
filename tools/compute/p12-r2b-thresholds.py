#!/usr/bin/env python3
"""TK-120 (v2, возврат Судьи 00:38): пороги R2-B из готовых rounds.csv замера W — только сделки B1 R2 (ключи 2750 из p12-r2b-b1keys.py),
лоты e112/e116 делятся на level_qty (= size_at_arm, /data/tk064/pool/m-<мес>/lvl/<сутки>.csv; 0/нет — вне популяции, спека :113).
Расширяющееся окно: месяц m — сделки B1 всех ПРЕДЫДУЩИХ полных месяцев; январь — по самому январю (PRECEDENTS 61b2aabd).
Мера e112 = tape_press_lots (30 с до исполнения входа) / level_qty, e116 = cxl_lots / level_qty; q33/q67 — ближайший ранг (как R1, tk064-r1cells).
W (e133) = медиана chase_wait_ms по сделкам B1 с определённой величиной; < 1000 мс — «не определена» (Г-133). W не нормируется.
Запуск: p12-r2b-thresholds.py <b1keys.csv> <w-каталог> <lvl-корень /data/tk064/pool> <выход-каталог>"""
import csv, glob, json, math, os, statistics, sys
from collections import defaultdict
KEYS, W, LV, OUT = sys.argv[1:5]
MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]


def rank(a, p):   # ближайший ранг без интерполяции
    a = sorted(a)
    return a[min(len(a) - 1, max(0, math.ceil(p * len(a)) - 1))]


keys = defaultdict(set)
for r in csv.DictReader(open(KEYS, encoding="utf-8")):
    keys[r["month"]].add((r["symbol"], int(r["t0_ms"])))
lvl = {}


def load_lvl(mo, day):
    if day in lvl:
        return lvl[day]
    d = defaultdict(list)
    try:
        for r in csv.DictReader(open(f"{LV}/m-{MON[int(mo[5:]) - 1]}/lvl/{day}.csv", encoding="utf-8")):
            d[r["symbol"]].append((int(float(r["arm_ms"])), float(r["size_at_arm"] or 0)))
    except FileNotFoundError:
        pass
    lvl[day] = d
    return d


by = {}
deltas = []
for mo in sorted(keys):
    d = by[mo] = {"tape": [], "cxl": [], "w": [], "n_keys": len(keys[mo]), "n_rounds": 0, "no_lvl": 0, "dl": 0, "dl_nowait": 0}
    seen = set()
    for f in sorted(glob.glob(f"{W}/{mo}-*/t-bid-btc4h-q1/rounds.csv")):
        day = f.split(W)[1].lstrip("/")[:10]
        with open(f, encoding="utf-8") as ih:
            for r in csv.DictReader(l for l in ih if not l.startswith("#")):
                k = (r["symbol"], int(r["t0_ns"]) // 1000000)
                if k not in keys[mo] or k in seen:
                    continue
                seen.add(k)
                d["n_rounds"] += 1
                c = load_lvl(mo, day).get(r["symbol"], [])
                hit = min(c, key=lambda x: abs(x[0] - k[1])) if c else None
                if hit is None or hit[1] <= 0:
                    d["no_lvl"] += 1
                else:
                    deltas.append(abs(hit[0] - k[1]))
                    d["tape"].append(float(r["tape_press_lots"]) / hit[1])
                    d["cxl"].append(float(r["cxl_lots"]) / hit[1])
                if r["reason"] == "deadline":
                    d["dl"] += 1
                    if r["chase_wait_ms"].strip():
                        d["w"].append(float(r["chase_wait_ms"]))
                    else:
                        d["dl_nowait"] += 1
    d["missing_in_w"] = len(keys[mo] - seen)
os.makedirs(OUT, exist_ok=True)
months = sorted(by)
summ = []
for i, m in enumerate(months):
    src = months[:i] if i else months[:1]
    cat = lambda k: [x for s in src for x in by[s][k]]
    row = {"month": m, "source_months": src, "n_b1_trades": sum(by[s]["n_rounds"] for s in src),
           "n_no_level_qty": sum(by[s]["no_lvl"] for s in src), "n_defined": len(cat("tape"))}
    for k, nm in (("tape", "e112"), ("cxl", "e116")):
        a = cat(k)
        row[f"{nm}_q33"], row[f"{nm}_q67"] = rank(a, 1 / 3), rank(a, 2 / 3)
    w = cat("w")
    row["n_deadline"] = sum(by[s]["dl"] for s in src)
    row["n_w_defined"] = len(w)
    row["W_ms_median"] = statistics.median(w) if w else None
    row["W_defined"] = bool(w and statistics.median(w) >= 1000)
    summ.append(row)
    with open(f"{OUT}/e-thresholds-{m}.csv", "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh, lineterminator="\n")
        wr.writerow(["key", "value"])
        for k, v in row.items():
            if k != "source_months":
                wr.writerow([k, v])
    json.dump(row, open(f"{OUT}/p12-thresholds-{m}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
per = {m: {k: by[m][k] for k in ("n_keys", "n_rounds", "missing_in_w", "no_lvl", "dl", "dl_nowait")} for m in months}
dd = sorted(deltas)
json.dump({"summary": summ, "per_month": per,
           "lvl_match_delta_ms": {"n": len(dd), "p50": dd[len(dd) // 2] if dd else None, "p99": dd[int(len(dd) * .99)] if dd else None,
                                  "max": dd[-1] if dd else None, "exact": sum(1 for x in dd if x == 0)}},
          open(f"{OUT}/summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(f"{OUT}/thresholds.done", "w").write("ok")
