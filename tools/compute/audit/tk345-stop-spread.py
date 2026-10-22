#!/usr/bin/env python3
"""TK-345 п.2 Судьи: стоп не внутри спреда от входа — по rounds.csv прохода 1–14.02 (набор base), без пересчёта.
Спред на входе в rounds нет; нижняя граница спреда = 1 тик, поэтому считаем долю кругов со стопом, где
|exit_px − entry_px| (цена исполнения стопа от входа) <= 1 тика (внутри минимального спреда) и <= 2 тиков.
Использование: tk345-stop-spread.py <instruments.csv> <выход.txt> <rounds.csv>..."""
import csv, collections, sys
inst, out = sys.argv[1], sys.argv[2]
tick = {r["symbol"]: float(r["tick_size"]) for r in csv.DictReader(l for l in open(inst) if not l.startswith("#"))}
acc = collections.defaultdict(collections.Counter)
for f in sys.argv[3:]:
    hd = None
    for line in open(f):
        if line.startswith("#"):
            continue
        row = line.rstrip("\n").split(",")
        if hd is None:
            hd = {h: i for i, h in enumerate(row)}
            continue
        if row[hd["reason"]] != "stop":
            continue
        form = row[hd["form"]]
        stop = form.split("-")[0] if not form.startswith("market") else form.split("-")[0] + "/" + form.split("-")[1]
        try:
            ep = float(row[hd["entry_vwap"]] or row[hd["entry_px"]]); xp = float(row[hd["exit_px"]]); tk = tick[row[hd["symbol"]]]
        except (ValueError, KeyError):
            acc[stop]["unparsed"] += 1
            continue
        d = abs(xp - ep) / tk
        a = acc[stop]
        a["n_stop"] += 1
        a["le1tick"] += d <= 1.0 + 1e-9
        a["le2tick"] += d <= 2.0 + 1e-9
        a["lt1tick"] += d < 1.0 - 1e-9
        a["bps_sum"] += abs(xp / ep - 1) * 1e4
with open(out, "w") as o:
    print("stop_form\tn_stop\tle1tick%\tlt1tick%\tle2tick%\tmean_dist_bps\tunparsed", file=o)
    for k, a in sorted(acc.items()):
        n = a["n_stop"] or 1
        print(f"{k}\t{a['n_stop']}\t{100*a['le1tick']/n:.2f}\t{100*a['lt1tick']/n:.2f}\t{100*a['le2tick']/n:.2f}\t{a['bps_sum']/n:.2f}\t{a['unparsed']}", file=o)
