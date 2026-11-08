"""П-40 G0: сводка часов бирж из g0_clocks3.jsonl. Для пары: n оценок (актив×сутки×час, corr_max >= 0,25), медиана, p10/p90, размах медиан по суткам,
неопределённость = max(размах медиан по (актив,сутки) / 2, 1,4826·MAD/√n) и разрешение метода (сетка 100 мс + параболическая подгонка).
Запуск: g0_clocks3_report.py <clocks3.jsonl> <выход.csv>"""
import sys, json, collections
import numpy as np
rows = [json.loads(l) for l in open(sys.argv[1])]
P = collections.defaultdict(list)
for r in rows:
    for p in r["pairs"]: P[(p["x"], p["y"])].append((r["asset"], r["day"], p["h"], p["ms"], p["corr"]))
out = ["x,y,n,n_ok,median_ms,p10_ms,p90_ms,unit_median_min,unit_median_max,unc_ms"]
for (x, y), v in sorted(P.items()):
    ok = [t for t in v if t[4] >= 0.25]
    if not ok: out.append(f"{x},{y},{len(v)},0,,,,,,"); continue
    ms = np.array([t[3] for t in ok]); med = float(np.median(ms)); mad = 1.4826 * float(np.median(np.abs(ms - med)))
    by = collections.defaultdict(list)
    for t in ok: by[(t[0], t[1])].append(t[3])
    um = [float(np.median(a)) for a in by.values()]
    unc = max((max(um) - min(um)) / 2, mad / np.sqrt(len(ms)))
    out.append(f"{x},{y},{len(v)},{len(ok)},{med:.1f},{np.percentile(ms,10):.1f},{np.percentile(ms,90):.1f},{min(um):.1f},{max(um):.1f},{unc:.1f}")
open(sys.argv[2], "w").write("\n".join(out) + "\n"); print("\n".join(out))
