"""Проверяющий arb-tokyo: сводка прогонов на выборке 48 монет — шкала ts (как отчёт) против cts; сверка с shift.pkl (s0); жизнь расхождения; разные задержки ног."""
import pickle, sys
from collections import defaultdict
import numpy as np
O = "/data/arb-tokyo-review"
LIFE_BPD = 40


def tt_tot(rows, tagsel):
    tot = defaultdict(float)
    byex = defaultdict(float)
    for k, st in rows.items():
        if k[0] != "tt" or "bingx" in (k[2], k[3]):
            continue
        th, lat, ex_, tag = k[4]
        if tag != tagsel:
            continue
        tot[(th, ex_, lat)] += st["usd_cap"]
        byex[(th, ex_, lat, f"{k[2]}-{k[3]}")] += st["usd_cap"]
    return tot, byex


def show(name, tot, ths=(20.0, 30.0), lats=(0, 1, 2, 5, 10, 20)):
    for th in ths:
        for ex_ in ("x1-t", "c-t"):
            print(f"{name} θ={th/100:g} {ex_}: " + " | ".join(f"L{l} {tot.get((th, ex_, l), 0):+.0f}" for l in lats))


sh = pickle.load(open("/data/arb-tokyo/shift.pkl", "rb"))
coins = set(open("/data/arb-tokyo/shift_coins.txt").read().strip().split(","))
t_sh, _ = tt_tot(sh["rows"], "s0")
show("shift.pkl s0", t_sh)
for k in ("bts", "bcts"):
    try:
        d = pickle.load(open(f"{O}/run-{k}.pkl", "rb"))
    except FileNotFoundError:
        continue
    t, bx = tt_tot(d["rows"], "-")
    show(f"run-{k}", t)
    for th in (20.0, 30.0):
        for ex_ in ("x1-t", "c-t"):
            for pe in ("binance-bybit", "binance-okx", "bybit-okx"):
                print(f"   {k} θ={th/100:g} {ex_} {pe}: " + " | ".join(f"L{l} {bx.get((th, ex_, l, pe), 0):+.0f}" for l in (1, 2, 5, 10)))
    pool = defaultdict(lambda: [0, 0, np.zeros(7)])
    for (coin, a, b), r in d["life"].items():
        v = r["th"].get(20.0)
        if v is None:
            continue
        p = pool[f"{a}-{b}"]
        p[0] = p[0] + v["hist"]; p[1] += v["n"]; p[2] += np.array(v["surv"]) * v["n"]
    for pe, (h, n, sv) in pool.items():
        if "bingx" in pe:
            continue
        c = np.cumsum(h)
        q = [10 ** ((np.searchsorted(c, x * c[-1]) + 0.5) / LIFE_BPD) for x in (.1, .5, .9)]
        print(f"   life {k} θ=0.2 {pe}: n {n} p10/50/90 {q[0]:.1f}/{q[1]:.1f}/{q[2]:.0f} ms; >1/2/5/10/20/50: " + "/".join(f"{100*x:.1f}" for x in (sv / n)[1:]))
for k in ("bts", "bcts", "eng"):
    try:
        d = pickle.load(open(f"{O}/leg-{k}.pkl", "rb"))
    except FileNotFoundError:
        continue
    agg = defaultdict(float)
    for (coin, a, b, th, ex_, sc), st in d["rows"].items():
        agg[(th, ex_, sc, "all")] += st["usd_cap"]
        agg[(th, ex_, sc, f"{a}-{b}")] += st["usd_cap"]
    print(f"== leg-{k}")
    for th in (20.0, 30.0):
        for ex_ in ("x1-t", "c-t"):
            for kind in ("bybit", "bybit+okx"):
                for n in (1, 2):
                    for pe in ("all", "binance-bybit", "binance-okx", "bybit-okx"):
                        print(f"   θ={th/100:g} {ex_} far={kind} near={n} {pe}: " + " | ".join(f"F{f} {agg.get((th, ex_, (kind, n, f), pe), 0):+.0f}" for f in (1, 2, 5, 10, 20, 35, 50)))
            for pe in ("all", "binance-bybit", "binance-okx", "bybit-okx"):
                print(f"   θ={th/100:g} {ex_} symmetric {pe}: " + " | ".join(f"L{L} {agg.get((th, ex_, ('bybit', L, L), pe), 0):+.0f}" for L in (1, 2, 5, 10)))
