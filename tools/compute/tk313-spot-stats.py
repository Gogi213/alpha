#!/usr/bin/env python3
"""TK-313 (П-21 §8): SR по месяцам, bootstrap по суткам, Холм по 16, спот против фьючерса (TK-289 f6v) на общих монето-сутках.
Вход: /data/tk0313/out/run-*/out/<сутки>/<клетка>/rounds.csv (спот), /data/tk0289/f6v/out/run-*/out/<сутки>/p18a6vv<S>_<U>-<мес>/rounds.csv (фьюч).
Выход: JSON в argv[1]. (б) = публичный спот 10/10 bps на ногу, как в docs/findings/tk313-spot-b1-2026-10-20.md."""
import csv, glob, json, os, sys, random
from collections import defaultdict

random.seed(313)
OUT = sys.argv[1]
SPOT = "/data/tk0313/out"
FUT = "/data/tk0289/f6v/out"
B = 5000
CELLS = ["s0"] + [f"g-s{s}-u{u}" for s in ("n", "25", "50", "100") for u in ("n", "1", "2", "3") if (s, u) != ("n", "n")]
FUTMAP = {}
for s in ("25", "50", "100"):
    for u, un in (("n", "none"), ("1", "30000"), ("2", "100000"), ("3", "300000")):
        FUTMAP[f"g-s{s}-u{u}"] = f"p18a6vv{s}_{un}"
FUTMAP["s0"] = "p18a6vv2_10000"  # ближайший аналог: поток >= 2 %, не равен S0


def read(path):
    rows = []
    with open(path, newline="") as f:
        lines = (l for l in f if not l.startswith("#"))
        for r in csv.DictReader(lines):
            rows.append(r)
    return rows


def pnl(r):
    qty = float(r["qty"]); px = float(r["entry_px"]); net = float(r["net_bps"])
    a = net / 1e4 * qty * px
    leg = 3.15 if r["reason"] in ("stop", "deadline") else 1.26
    b = (net + 1.26 + leg - 20.0) / 1e4 * qty * px
    return a, b


def load_spot():
    d = {}  # cell -> {(day): [(sym, a, b)]}
    days = {}
    for rd in sorted(glob.glob(f"{SPOT}/run-*")):
        day = rd.rsplit("-", 3)[-3] + "-" + rd.rsplit("-", 2)[-2] + "-" + rd.rsplit("-", 1)[-1]
        outd = f"{rd}/out/{day}"
        if not os.path.isdir(outd):
            continue
        days[day] = True
        for c in CELLS + ["ctl-nn"]:
            p = f"{outd}/{c}/rounds.csv"
            if not os.path.exists(p):
                continue
            for r in read(p):
                a, b = pnl(r)
                d.setdefault(c, {}).setdefault(day, []).append((r["symbol"], a, b))
    return d, sorted(days)


def load_fut():
    d = {}
    days = set()
    for rd in sorted(glob.glob(f"{FUT}/run-*")):
        day = "-".join(rd.rsplit("-", 3)[-3:])
        outd = f"{rd}/out/{day}"
        if not os.path.isdir(outd):
            continue
        days.add(day)
        mon = day[:7]
        for key in set(FUTMAP.values()):
            p = f"{outd}/{key}-{mon}/rounds.csv"
            if not os.path.exists(p):
                continue
            for r in read(p):
                a, _ = pnl(r)
                d.setdefault(key, {}).setdefault(day, []).append((r["symbol"], a))
    return d, days


def sr(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / v ** 0.5 if v > 0 else 0.0


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(q * len(v))))]


spot, sdays = load_spot()
months = sorted({d[:7] for d in sdays})
bymonth = {m: [d for d in sdays if d[:7] == m] for m in months}
res = {"days": len(sdays), "cells": {}}
pvals = {}
for c in CELLS + ["ctl-nn"]:
    dd = spot.get(c, {})
    daily_b = {d: sum(x[2] for x in dd.get(d, [])) for d in sdays}
    daily_a = {d: sum(x[1] for x in dd.get(d, [])) for d in sdays}
    mon = {}
    for m in months:
        xs = [daily_b[d] for d in bymonth[m]]
        ntr = sum(len(dd.get(d, [])) for d in bymonth[m])
        mon[m] = {"n": ntr, "sum_b": round(sum(xs), 1), "sum_a": round(sum(daily_a[d] for d in bymonth[m]), 1),
                  "sr_day": round(sr(xs), 3), "days_with_trade": sum(1 for d in bymonth[m] if dd.get(d))}
    allx = [daily_b[d] for d in sdays]
    srs, sums = [], []
    for _ in range(B):
        xs = []
        for m in months:
            ds = bymonth[m]
            xs += [daily_b[random.choice(ds)] for _ in ds]
        srs.append(sr(xs)); sums.append(sum(xs))
    p = (1 + sum(1 for s in srs if s <= 0)) / (B + 1)
    pvals[c] = p
    res["cells"][c] = {"months": mon, "n": sum(v["n"] for v in mon.values()), "sum_b": round(sum(allx), 1),
                       "sum_a": round(sum(daily_a.values()), 1), "months_pos_b": sum(1 for v in mon.values() if v["sum_b"] > 0),
                       "sr_day": round(sr(allx), 3), "sr_ci90": [round(pct(srs, .05), 3), round(pct(srs, .95), 3)],
                       "sum_ci90": [round(pct(sums, .05), 1), round(pct(sums, .95), 1)], "p_boot": round(p, 4),
                       "days_with_trade": sum(1 for d in sdays if dd.get(d))}
# Холм по 16 (без ctl-nn)
order = sorted(CELLS, key=lambda c: pvals[c])
prev = 0.0
for i, c in enumerate(order):
    adj = min(1.0, max(prev, pvals[c] * (len(CELLS) - i)))
    prev = adj
    res["cells"][c]["p_holm"] = round(adj, 4)
    res["cells"][c]["transfer_b"] = bool(res["cells"][c]["months_pos_b"] >= 6 and res["cells"][c]["sr_ci90"][0] > 0 and adj < 0.10)

# спот против фьючерса (а), общие монето-сутки
fut, fdays = load_fut()
common = [d for d in sdays if d in fdays]
res["common_days"] = len(common)
# общие символы: есть сделка хотя бы в одной фьюч-клетке и в одной спот-клетке
fsyms = {x[0] for k in fut for d in fut[k] for x in fut[k][d]}
ssyms = {x[0] for c in spot for d in spot[c] for x in spot[c][d]}
cs = fsyms & ssyms
res["common_syms"] = len(cs); res["fut_syms"] = len(fsyms); res["spot_syms"] = len(ssyms)
cmp_ = {}
for c, k in FUTMAP.items():
    fa = [(d, x) for d in common for x in fut.get(k, {}).get(d, []) if x[0] in cs]
    sa = [(d, x) for d in common for x in spot.get(c, {}).get(d, []) if x[0] in cs]
    row = {"fut_key": k, "fut_n": len(fa), "fut_sum_a": round(sum(x[1] for _, x in fa), 1),
           "spot_n": len(sa), "spot_sum_a": round(sum(x[1] for _, x in sa), 1),
           "fut_months_pos": sum(1 for m in months if sum(x[1] for d, x in fa if d[:7] == m) > 0),
           "spot_months_pos": sum(1 for m in months if sum(x[1] for d, x in sa if d[:7] == m) > 0)}
    cmp_[c] = row
res["vs_fut"] = cmp_
json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
print("ok", OUT)
