#!/usr/bin/env python3
"""TK-114 v2 (дерево волны A p12r2a; B1 из p12r2, как принятое). П-13 §7 + §6 п.2: $ по месяцам и монетам, Шарп, из closessym половин А (data/tk114/v2/A/r2) и Б (data/tk114/v2/B/r2). Вывод docs/findings/p13-final-2026-10-09.{md,csv}."""
import json, math, csv, datetime as dt
from collections import defaultdict
import numpy as np
P0 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
F = {"B1": "t-bid-btc4h-q1@" + P0, "g93-u1-K3": "t-bid-btc4h-q1@" + P0 + "-pynw3u3"}
OUT = "v2"
MO = [f"2026-{m:02d}" for m in range(1, 10)]
split = json.load(open("docs/findings/p13-split-2026-10-09.json", encoding="utf-8"))
HALF = {s: h for h in "AB" for s in split[h]}
def load(root, cap):
    out = {c: [] for c in F}
    for m in MO:
        d = json.load(open(f"{root}/closessym-cap{cap}-{m}.json"))
        for c, f in F.items():
            for per in d.get(f, {}).values():
                for lst in per.values():
                    out[c] += [(int(a), float(b), s) for a, b, s in lst]
    return out
day = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
rows = []; L = []
def sr(tr):
    by = defaultdict(float)
    for ms, v, _ in tr: by[day(ms)] += v
    cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range(273)]
    a = np.array([by.get(d, 0.0) for d in cal]); s = a.std(ddof=1)
    return (a.mean() / s if s else 0.0)
for cap, mode in (("0", "free"), ("3", "B2")):
    A = load("data/tk114/v2/A/r2", cap); B = load("data/tk114/v2/B/r2", cap)
    L.append(f"\n## Режим {mode} (окно 01.01–30.09; октябрь не считан)\n")
    L.append("| клетка | половина | сделок | $ янв–сен | из них январь | худший месяц $ | мес. прибыльных | Шарп сут. | ×√365 |\n|---|---|---|---|---|---|---|---|---|")
    for c in F:
        for nm, tr in ((("A", A[c]), ("B", B[c]), ("A+B", A[c] + B[c])) if mode == "free" else (("A", A[c]), ("B", B[c]))):  # B2: половины отдельно, без суммы (Судья п.4)
            mon = {m: sum(v for ms, v, _ in tr if day(ms)[:7] == m) for m in MO}
            s = sr(tr)
            L.append(f"| {c} | {nm} | {len(tr)} | {sum(mon.values()):+.0f} | {mon['2026-01']:+.0f} | {min(mon.values()):+.0f} ({min(mon, key=mon.get)}) | {sum(v > 0 for v in mon.values())}/9 | {s:.4f} | {s*math.sqrt(365):.3f} |")
            rows.append([mode, c, nm, len(tr), round(sum(mon.values()), 1)] + [round(mon[m], 1) for m in MO] + [round(s, 5)])
    L.append("\n$ по месяцам (A+B):\n\n| клетка | " + " | ".join(m[5:] for m in MO) + " |\n|---|" + "---|" * 9)
    for c in F:
        tr = A[c] + B[c]; L.append(f"| {c} | " + " | ".join(f"{sum(v for ms, v, _ in tr if day(ms)[:7] == m):+.0f}" for m in MO) + " |")
    # §6 п.2: отбор неудачников по янв–май, проверка июн–сен
    for c in F:
        tr = A[c] + B[c]; co = defaultdict(lambda: [0.0, 0.0])
        for ms, v, s in tr: co[s][0 if day(ms)[:7] <= "2026-05" else 1] += v
        neg1 = [s for s, v in co.items() if v[0] < 0]
        stay = [s for s in neg1 if co[s][1] < 0]
        allneg2 = sum(v[1] < 0 for v in co.values())
        L.append(f"\n§6 п.2 · {mode} · {c}: монет {len(co)}; минус на янв–май {len(neg1)}; из них в минусе июн–сен {len(stay)} ({len(stay)/max(1,len(neg1)):.0%}) против доли минусовых монет в июн–сен в целом {allneg2}/{len(co)} ({allneg2/len(co):.0%}); $ июн–сен отобранных {sum(co[s][1] for s in neg1):+.0f}")
    # разрез по монетам для победителя
    co = defaultdict(lambda: [0.0, 0.0, 0])
    for k, c in enumerate(F):
        for ms, v, s in A[c] + B[c]: co[s][k] += v
    d = sorted(co.items(), key=lambda kv: kv[1][1] - kv[1][0])
    tot = sum(v[1] - v[0] for _, v in d)
    L.append(f"\nРазрез по монетам · {mode}: прирост $ победителя над B1 = {tot:+.0f}; монет с приростом>0: {sum(v[1]-v[0]>0 for _,v in d)}/{len(d)}; топ-5: " + ", ".join(f"{s} {v[1]-v[0]:+.0f}" for s, v in d[-5:][::-1]) + "; низ-5: " + ", ".join(f"{s} {v[1]-v[0]:+.0f}" for s, v in d[:5]))
    with open(f"docs/findings/p13-coins-{mode}-2026-10-09.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["sym", "half", "usd_B1", "usd_g93-u1-K3", "delta"])
        for s, v in d: w.writerow([s, HALF.get(s, "?"), round(v[0], 1), round(v[1], 1), round(v[1] - v[0], 1)])
# ---- полный пул (одна симуляция), В-210, пик
import re as _re
def full(root, cap):
    out = {c: [] for c in F}
    for m in MO:
        for pre, cs in (("", ["g93-u1-K3"]), ("b1-", ["B1"])):
            d = json.load(open(f"{root}/{pre}closessym-cap{cap}-{m}.json"))
            for c in cs:
                for per in d.get(F[c], {}).values():
                    for lst in per.values(): out[c] += [(int(a), float(b), s) for a, b, s in lst]
    return out
def peak(root, cap):
    r = {}
    for pre, c in (("", "g93-u1-K3"), ("b1-", "B1")):
        pk = []
        for m in MO:
            for ln in open(f"{root}/{pre}psim-cap{cap}-{m}.txt", encoding="utf-8").read().splitlines()[1:]:
                if ln.strip(): pk.append(int(ln.split()[-2]))
        r[c] = max(pk)
    return r
L.append("\n## Полный пул, одна симуляция (дерево волны A; B1 — p12r2): v171c (--drop TRUMP/TRX/BCH) и v171b (все монеты, В-210)\n")
L.append("| режим | пул | клетка | сделок | $ янв–сен | из них январь | худший месяц $ | Шарп сут. | ×√365 | пик $ (экспозиция) |\n|---|---|---|---|---|---|---|---|---|---|")
for mode, cap in (("free", "0"), ("B2", "3")):
    for pool, root in (("v171c", "data/tk114/v2/F"), ("v171b", "data/tk114/v2/G")):
        T = full(root, cap); pk = peak(root, cap)
        for c in F:
            tr = T[c]; mon = {m: sum(v for ms, v, _ in tr if day(ms)[:7] == m) for m in MO}; s = sr(tr)
            L.append(f"| {mode} | {pool} | {c} | {len(tr)} | {sum(mon.values()):+.0f} | {mon['2026-01']:+.0f} | {min(mon.values()):+.0f} ({min(mon, key=mon.get)}) | {s:.4f} | {s*math.sqrt(365):.3f} | {pk[c]} |")
            rows.append([mode, c, "full-" + pool, len(tr), round(sum(mon.values()), 1)] + [round(mon[m], 1) for m in MO] + [round(s, 5)])
    if mode == "free":
        Af = load("data/tk114/v2/A/r2", "0"); Bf = load("data/tk114/v2/B/r2", "0"); Ff = full("data/tk114/v2/F", "0")
        for c in F:
            L.append(f"\nСверка free {c}: A+B = {sum(v for _,v,_ in Af[c]+Bf[c]):+.1f} $ / {len(Af[c]+Bf[c])} сд.; полный пул v171c = {sum(v for _,v,_ in Ff[c]):+.1f} $ / {len(Ff[c])} сд.")
open("docs/findings/p13-final-2026-10-09.md.part", "w", encoding="utf-8", newline="").write("\n".join(L) + "\n")
print("\n".join(L))
