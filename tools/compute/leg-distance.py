#!/usr/bin/env python3
"""Где ставить нижнюю ногу: вероятность исполнения и условный результат по расстоянию от стены (в %)."""
import csv, glob, os, sys, math, statistics, collections
setdir, touches_dir = sys.argv[1], sys.argv[2]
usd = float(sys.argv[3]) if len(sys.argv) > 3 else 1000.0
FEES = 4.41
BINS = [(0, 0, "в упор (0 %)"), (0.0001, 2, "0–0,02 %"), (2.0001, 5, "0,02–0,05 %"), (5.0001, 10, "0,05–0,1 %"), (10.0001, 15, "0,1–0,15 %"), (15.0001, 20, "0,15–0,2 %")]
def binof(d):
    for lo, hi, name in BINS:
        if lo <= d <= hi: return name
    return None
# markouts per touch
mk = {}
for day_dir in glob.glob(os.path.join(touches_dir, "20*")):
    for p in glob.glob(os.path.join(day_dir, "touches-*.csv")):
        sym = os.path.basename(p)[8:-4]
        for r in csv.DictReader(open(p)):
            mk[(sym, int(r["start_ms"]), int(r["price_tick"]))] = {h: (float(r[h]) if r[h] not in ("", None) else None) for h in ("m_10m", "m_1h", "m_2h")}
slots = ["t0", "pre300", "post300", "post1800"]
# obs[slot][bin] -> list of (fill_frac, net10m, net1h, net2h, crossed)
obs = {s: collections.defaultdict(list) for s in slots}
n_touch = 0
for p in sorted(glob.glob(os.path.join(setdir, "capacity-*.csv"))):
    rows = list(csv.DictReader(open(p)))
    if not rows: continue
    tick_px = float(rows[0]["tick_px"]); lot = float(rows[0]["lot_qty"])
    per = collections.defaultdict(list)
    for r in rows: per[(r["symbol"], int(r["start_ms"]), int(r["price_tick"]))].append(r)
    for key, rs in per.items():
        n_touch += 1
        m = mk.get(key, {})
        P = key[2]
        for slot in slots:
            qcol = "q_t0" if slot.startswith("post") or slot == "t0" else f"q_{slot}"
            oppcol = "opp_t0" if slot.startswith("post") or slot == "t0" else f"opp_{slot}"
            bestcol = "best_t0" if slot.startswith("post") or slot == "t0" else f"best_{slot}"
            soldcol = {"t0": "sold_touch", "pre300": None, "post300": "sold_post300", "post1800": "sold_post1800"}[slot]
            perbin = collections.defaultdict(list)
            for r in rs:
                d = float(r["dist_bps"])
                b = binof(d)
                if b is None: continue
                q = int(r[qcol])
                if q < 0: continue
                tick = int(r["tick"]); opp = int(r[oppcol]); best = int(r[bestcol])
                crossed = opp >= 0 and tick >= opp
                if slot == "pre300":
                    sold = int(r["sold_pre300"]) + int(r["sold_touch"])
                else:
                    sold = int(r[soldcol])
                px = tick * tick_px
                want = math.floor(usd / (px * lot))
                if want <= 0: continue
                got = 0 if crossed else max(0, min(want, sold - q))
                frac = got / want
                mid = (best + opp) / 2 if (best > 0 and opp > 0) else P
                prem = (tick - mid) / mid * 1e4
                nets = {}
                for h in ("m_10m", "m_1h", "m_2h"):
                    v = m.get(h)
                    nets[h] = None if v is None else v - prem - FEES
                perbin[b].append((frac, nets, crossed))
            for b, xs in perbin.items():
                # одна запись на касание и корзину: среднее по тикам корзины
                frac = statistics.mean(x[0] for x in xs)
                cross = statistics.mean(1.0 if x[2] else 0.0 for x in xs)
                # условный результат: по исполненным тикам
                filled = [x for x in xs if x[0] > 0]
                nets = {}
                for h in ("m_10m", "m_1h", "m_2h"):
                    vals = [x[1][h] for x in filled if x[1][h] is not None]
                    nets[h] = statistics.mean(vals) if vals else None
                obs[slot][b].append((frac, nets, cross))
print(f"набор {setdir}: касаний {n_touch}, нога ${usd:.0f}, правило «наторговано − очередь», net = markout − премия к середине − 4.41 bps комиссий")
for slot in slots:
    print(f"\n## слот {slot}")
    print("| расстояние от стены | касаний | через спред | хоть что-то | целиком | доля ср. | net 10м (исп.) | net 1ч (исп.) | net 2ч (исп.) |\| исп. | net 1ч \| исп. | net 2ч \| исп. | ожидание на постановку 1ч |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lo, hi, name in BINS:
        xs = obs[slot].get(name, [])
        if not xs: print(f"| {name} | 0 | | | | | | | | |"); continue
        n = len(xs)
        any_ = sum(1 for x in xs if x[0] > 0) / n
        full = sum(1 for x in xs if x[0] >= 0.99) / n
        cross = statistics.mean(x[2] for x in xs)
        mf = statistics.mean(x[0] for x in xs)
        def cond(h):
            v = [x[1][h] for x in xs if x[0] > 0 and x[1][h] is not None]
            return (statistics.mean(v), len(v)) if v else (None, 0)
        c10, c1, c2 = cond("m_10m"), cond("m_1h"), cond("m_2h")
        fmt = lambda c: f"{c[0]:+.1f} (n={c[1]})" if c[0] is not None else "—"
        ev = f"{any_ * c1[0]:+.2f}" if c1[0] is not None else "—"
        print(f"| {name} | {n} | {cross:.0%} | {any_:.0%} | {full:.0%} | {mf:.0%} | {fmt(c10)} | {fmt(c1)} | {fmt(c2)} | {ev} bps |")
