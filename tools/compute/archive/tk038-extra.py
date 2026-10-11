#!/usr/bin/env python3
"""TK-038 доп. свод (Судья 04.10): внутридневные снимки, X1 ppm, T4 задержка, T3 откаты exch_ts, по месяцам.
Использование: tk038-extra.py <родитель каталогов вывода lob validate>"""
import csv, sys, glob, collections
d = sys.argv[1]
M = collections.defaultdict(collections.Counter)
days = collections.defaultdict(set); p50 = collections.defaultdict(list); big = []
seen = set()
for p in sorted(glob.glob(f"{d}/*/hours.csv")):
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            k = (r["symbol"], r["day"], r["hour"])
            if k in seen: continue
            seen.add(k)
            m = r["day"][:7]; c = M[m]
            days[m].add((r["symbol"], r["day"]))
            sn = int(r["snapshots"]); c["snaps"] += sn
            if int(r["snapshot_drift"]) > 0:
                c["drift_h"] += 1; c["drift_lv"] += int(r["snapshot_drift_levels"])
            c["trades"] += int(r["trades"]); c["viol"] += int(r["trades_violations"]); c["oor"] += int(r["trades_out_of_range"])
            c["indet"] += int(r["trades_indeterminate"])
            p50[m].append(float(r["lat_p50_ms_ub"])); c["lat_max"] = max(c["lat_max"], float(r["lat_max_ms"]))
            b = float(r["max_backward_ms"])
            if b > 0: c["back_h"] += 1
            if b > 100: c["b100"] += 1
            if b > 1000: c["b1000"] += 1; big.append((b, r["symbol"], r["day"], r["hour"]))
            c["bmax"] = max(c["bmax"], b)
print("месяц; монето-суток; снимков всего; внутридневных (сверх 1/сутки); часов с дрейфом; ср. уровней расхождения; сделок; вне видимой книги; ppm; тест 3 (цена внутри, уровень не держала); ppm; неопр.; lat_p50 медиана по часам, мс; lat_max, мс; часов с откатом; >100 мс; >1 с; max откат, мс")
for m in sorted(M):
    c = M[m]; nd = len(days[m]); s = sorted(p50[m])
    intra = c["snaps"] - nd
    print(f"{m}; {nd}; {c['snaps']}; {intra if m < '2026-08' else str(intra)+' (не измерено)'}; {c['drift_h']}; "
          f"{c['drift_lv']/c['drift_h'] if c['drift_h'] else 0:.0f}; {c['trades']}; {c['oor']}; {1e6*c['oor']/max(c['trades'],1):.1f}; {c['viol']}; {1e6*c['viol']/max(c['trades'],1):.1f}; {c['indet']}; "
          f"{s[len(s)//2]:.0f}; {c['lat_max']:.0f}; {c['back_h']}; {c['b100']}; {c['b1000']}; {c['bmax']:.0f}")
print("\nоткат exch_ts > 1 с (мс, символ, сутки, час):")
for b in sorted(big, reverse=True): print(f"{b[0]:.0f} {b[1]} {b[2]} {b[3]}")
