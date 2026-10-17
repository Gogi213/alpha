#!/usr/bin/env python3
"""TK-279 (П-17 §0 п.3, Г-81, вариант Б): пороги Q_m(q,W) = q-квантиль меры ворот (отмены за [sent, sent+W] ÷ size_at_touch) по
касаниям B1 СВОЕГО месяца, мера — `gate-probe.csv` пробы `entry_gate=cxl<W>qprobe` (tk279-g81-probe.sh). Квантиль — ближайший
ранг без интерполяции (как p17-btc4h-thresholds.py), q ∈ {5,10,20,33,50,67,80,90,95}, округление до 4 знаков. Охват §4 =
строк пробы / n_signals из forms.csv набора (касания, у которых ворота проверены, к сигналам B1).
  p17-g81-thresholds.py <каталог прохода out> <P-17-thresholds.csv> <P-17-g81-coverage.csv>   (дописывает строки Г-81, повтор — замена)"""
import csv, glob, math, os, sys
from collections import defaultdict

QS = (5, 10, 20, 33, 50, 67, 80, 90, 95)
out_dir, thr_path, cov_path = sys.argv[1:4]
meas = defaultdict(list)        # (месяц, W) -> [мера]
sig = defaultdict(int)          # (месяц, W) -> n_signals
for p in glob.glob(f"{out_dir}/run-*/out/*/cxlprobe*/gate-probe.csv") + glob.glob(f"{out_dir}/run-*/w-*/b5/out-*-tape/cxlprobe*/gate-probe.csv"):
    with open(p, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            meas[(r["day_utc"][:7], int(r["w"]))].append(float(r["measure"]))
for p in glob.glob(f"{out_dir}/run-*/out/*/cxlprobe*/forms.csv") + glob.glob(f"{out_dir}/run-*/w-*/b5/out-*-tape/cxlprobe*/forms.csv"):
    w = int(os.path.basename(os.path.dirname(p))[len("cxlprobe"):])
    with open(p, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(l for l in fh if not l.startswith("#")):   # forms.csv начинается строкой «# lob bounce-grid: …»
            sig[(r["day_utc"][:7], w)] += int(r["n_signals"])
rows = []
for (m, w), v in sorted(meas.items()):
    v.sort(); n = len(v)
    for q in QS:
        rows.append(("Г-81", m, f"cxl_w{w}_over_size", q, f"{v[max(0, math.ceil(q / 100 * n) - 1)]:.4f}", n))
keep = [l for l in csv.reader(open(thr_path, encoding="utf-8", newline="")) if l and l[0] != "Г-81"]
with open(thr_path, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n"); wr.writerows(keep); wr.writerows(rows)
with open(cov_path, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n"); wr.writerow(("month", "w", "n_signals", "n_measured", "share"))
    for (m, w), v in sorted(meas.items()):
        s = sig.get((m, w), 0); wr.writerow((m, w, s, len(v), f"{len(v) / s:.4f}" if s else ""))
print(f"Г-81: {len(rows)} порогов, {len(meas)} пар месяц×W, {sum(len(v) for v in meas.values())} мер")
