#!/usr/bin/env python3
"""TK-120: пороги R2-B из готовых rounds.csv замера W (форма chase86400000, --tape-log 30) — без пересчёта.
Расширяющееся окно: месяц m берёт сделки B1 всех ПРЕДЫДУЩИХ полных месяцев; январь — по самому январю (PRECEDENTS 61b2aabd).
Мера e112 = tape_press_lots (лоты за 30 с до исполнения входа), e116 = cxl_lots (отмены нашей стороны), q33/q67 = терцили;
W (e133) = медиана chase_wait_ms по сделкам, где она определена; < 1000 мс — «не определена» (спека Г-133).
Запуск: p12-r2b-thresholds.py <w-каталог> <выход-каталог>"""
import csv, glob, json, os, sys
import statistics

def pct(a, p):   # линейная интерполяция, как numpy.percentile
    a = sorted(a); k = (len(a) - 1) * p / 100; i = int(k)
    return a[i] + (a[min(i + 1, len(a) - 1)] - a[i]) * (k - i)
W, OUT = sys.argv[1], sys.argv[2]
by = {}   # месяц -> {tape:[], cxl:[], w:[], n, censored, reasons}
for f in sorted(glob.glob(f"{W}/20*/t-bid-btc4h-q1/rounds.csv")):
    mo = f.split(W)[1].lstrip("/")[:7]
    d = by.setdefault(mo, {"tape": [], "cxl": [], "w": [], "n": 0, "dl": 0, "dl_nowait": 0})
    with open(f, encoding="utf-8") as ih:
        for r in csv.DictReader(l for l in ih if not l.startswith("#")):
            d["n"] += 1
            d["tape"].append(float(r["tape_press_lots"])); d["cxl"].append(float(r["cxl_lots"]))
            if r["reason"] == "deadline":
                d["dl"] += 1
                if r["chase_wait_ms"].strip(): d["w"].append(float(r["chase_wait_ms"]))
                else: d["dl_nowait"] += 1
os.makedirs(OUT, exist_ok=True)
months = sorted(by)
summ = []
for i, m in enumerate(months):
    src = months[:i] if i else months[:1]
    cat = lambda k: [x for s in src for x in by[s][k]]
    row = {"month": m, "source_months": src, "n_trades": int(sum(by[s]["n"] for s in src))}
    for k, nm in (("tape", "e112"), ("cxl", "e116")):
        a = cat(k)
        row[f"{nm}_q33"], row[f"{nm}_q67"] = [pct(a, p) for p in (100 / 3, 200 / 3)]
    w = cat("w")
    row["n_deadline"] = int(sum(by[s]["dl"] for s in src)); row["n_w_defined"] = int(len(w))
    row["n_deadline_no_wait"] = int(sum(by[s]["dl_nowait"] for s in src))
    row["W_ms_median"] = statistics.median(w) if w else None
    row["W_defined"] = bool(w and statistics.median(w) >= 1000)
    summ.append(row)
    with open(f"{OUT}/e-thresholds-{m}.csv", "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh, lineterminator="\n"); wr.writerow(["key", "value"])
        for k, v in row.items():
            if k != "source_months": wr.writerow([k, v])
    json.dump(row, open(f"{OUT}/p12-thresholds-{m}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = {m: {"n": by[m]["n"], "deadline": by[m]["dl"], "dl_no_wait": by[m]["dl_nowait"], "w_med_own_month": statistics.median(by[m]["w"]) if by[m]["w"] else None} for m in months}
json.dump({"summary": summ, "per_month": tot}, open(f"{OUT}/summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(f"{OUT}/thresholds.done", "w").write("ok")
