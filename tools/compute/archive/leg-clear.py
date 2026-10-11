#!/usr/bin/env python3
"""Исполнение «в упор» и над стеной: стена держит (лучший бид на стене или выше после выборки очереди)
или насквозь (ниже стены) — доли и условный результат по расстоянию от стены."""
import csv, glob, os, sys, math, statistics, collections
setdir, touches_dir = sys.argv[1], sys.argv[2]
POST = int(sys.argv[3]) if len(sys.argv) > 3 else 1800
FEES = 4.41
BINS = [(0, 0, "в упор (0 %)"), (0.0001, 2, "0–0,02 %"), (2.0001, 5, "0,02–0,05 %"), (5.0001, 10, "0,05–0,1 %"), (10.0001, 20, "0,1–0,2 %")]
def binof(d):
    for lo, hi, name in BINS:
        if lo <= d <= hi: return name
mk = {}
for day_dir in glob.glob(os.path.join(touches_dir, "20*")):
    for p in glob.glob(os.path.join(day_dir, "touches-*.csv")):
        sym = os.path.basename(p)[8:-4]
        for r in csv.DictReader(open(p)):
            mk[(sym, int(r["start_ms"]), int(r["price_tick"]))] = {h: (float(r[h]) if r[h] else None) for h in ("m_10m", "m_1h", "m_2h")}
# per bin: lists of (cls, net1h, net2h, clear_s); cls in {"hold","through","none"}
res = collections.defaultdict(list)
n_touch = 0
for p in sorted(glob.glob(os.path.join(setdir, "capacity-*.csv"))):
    rows = list(csv.DictReader(open(p)))
    if not rows: continue
    per = collections.defaultdict(list)
    for r in rows: per[(r["symbol"], int(r["start_ms"]), int(r["price_tick"]))].append(r)
    for key, rs in per.items():
        n_touch += 1
        m = mk.get(key, {})
        P = key[2]
        perbin = collections.defaultdict(list)
        for r in rs:
            b = binof(float(r["dist_bps"]))
            if b is None: continue
            tick = int(r["tick"]); opp = int(r["opp_t0"]); best = int(r["best_t0"])
            if opp >= 0 and tick >= opp:  # через спред в t0 — не поставить
                continue
            cl = int(r["clear_ms"])
            if cl < 0 or cl > POST * 1000:
                cls = "none"
            else:
                ba = int(r["best_after_clear"])
                cls = "unknown" if ba < 0 else ("hold" if ba >= P else "through")
            mid = (best + opp) / 2 if (best > 0 and opp > 0) else P
            prem = (tick - mid) / mid * 1e4
            n1 = None if m.get("m_1h") is None else m["m_1h"] - prem - FEES
            n2 = None if m.get("m_2h") is None else m["m_2h"] - prem - FEES
            perbin[b].append((cls, n1, n2, cl / 1000 if cl >= 0 else None))
        for b, xs in perbin.items():
            # одна запись на касание и корзину: берём ближайший к стене тик корзины
            res[b].append(xs[0])
print(f"набор {setdir}: касаний {n_touch}; заявка живёт {POST} с от касания; выборка очереди = наторговано на тике > очереди t0")
print("| расстояние | поставлено | очередь выбрана | из них стена держит | насквозь | до выборки, мед. с | net 1ч держит | net 1ч насквозь | net 2ч держит | net 2ч насквозь |")
print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for lo, hi, name in BINS:
    xs = res.get(name, [])
    if not xs: continue
    n = len(xs)
    cleared = [x for x in xs if x[0] in ("hold", "through")]
    hold = [x for x in cleared if x[0] == "hold"]; thr = [x for x in cleared if x[0] == "through"]
    def mean(v, i):
        vv = [x[i] for x in v if x[i] is not None]
        return f"{statistics.mean(vv):+.1f} (n={len(vv)})" if vv else "—"
    med = statistics.median([x[3] for x in cleared]) if cleared else float("nan")
    print(f"| {name} | {n} | {len(cleared)} ({len(cleared)/n:.0%}) | {len(hold)} ({(len(hold)/len(cleared) if cleared else 0):.0%}) | {len(thr)} | {med:.0f} | {mean(hold,1)} | {mean(thr,1)} | {mean(hold,2)} | {mean(thr,2)} |")
