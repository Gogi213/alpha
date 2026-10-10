#!/usr/bin/env python3
"""TK-065: сводка волны R2 по (месяц, набор, форма) -> /data/tk065/r2-summary.csv"""
import collections, csv, glob, os

days = [l.split() for l in open("/data/tk065/days/days.tsv")]
agg = collections.defaultdict(lambda: [0, 0, 0, 0.0, 0, 0, set()])
bad = []
def read(f):
    return [r for r in csv.DictReader(l for l in open(f, encoding="utf-8") if not l.startswith("#"))]


for d, _m in days:
    has_sup = os.path.isdir(f"/data/tk065/ws-{d}")
    for w, ws, pat in (("w", "ws", f"/data/tk065/%s-{d}/b5/r2/{d}/*/forms.csv"), ("wt", "wst", f"/data/tk065/%s-{d}/b5/r2t/{d}/*/forms.csv")):
        fs = glob.glob(pat % w)
        if not fs:
            bad.append((d, pat % w))
        for f in fs:
            st = os.path.basename(os.path.dirname(f))
            rows = {(r["symbol"], r["form"]): r for r in read(f)}
            if has_sup:  # слой досчёта TK-040 (монеты, которых нет в основном доме) перекрывает строки основного
                fsup = f.replace(f"/{w}-", f"/{ws}-", 1)
                if os.path.exists(fsup):
                    rows.update({(r["symbol"], r["form"]): r for r in read(fsup)})
            for r in rows.values():
                a = agg[(d[:7], st, r["form"])]
                a[0] += int(r["n_signals"]); a[1] += int(r["n_submitted"]); a[2] += int(r["n_fills"])
                a[3] += float(r["sum_net_bps"]); a[4] += int(r["n_stop"]); a[5] += int(r["n_take"])
                if int(r["n_fills"]):
                    a[6].add(d)
with open("/data/tk065/r2-summary.csv", "w", newline="\n") as o:
    w = csv.writer(o)
    w.writerow(["month", "set", "form", "n_signals", "n_submitted", "n_fills", "sum_net_bps", "n_stop", "n_take", "days_with_fills"])
    for k in sorted(agg):
        a = agg[k]
        w.writerow([*k, a[0], a[1], a[2], f"{a[3]:.2f}", a[4], a[5], len(a[6])])
print(len(days), "суток;", len(agg), "строк;", len(bad), "пропусков", bad[:3])
