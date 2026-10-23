#!/usr/bin/env python3
"""TK-373: net догона пула по fee tier и газу. Вход: fees.json (GeckoTerminal pool_fee_percentage по пулам pools-all.json),
out4/coin/*.tsv.gz (tk373-rev.py), USD-корень (имена монет по сетям). Газ — сведён к бп от размера свопа (GAS_USD / SIZE).
Круг = вход+выход по DEX: 2*fee + 2*gas; один конец (хедж на Bybit) = fee + gas.
usage: tk373-fees.py <fees.json> <rev-out-dir> <usd-root> <out-prefix>"""
import sys, os, glob, gzip, csv, json, collections
import numpy as np
FEES, D, USD, PFX = sys.argv[1:5]
GAS_USD = {"ethereum": 0.05, "bsc": 0.01, "base": 0.005}   # ~150k газа; замер eth_feeHistory/eth_gasPrice публичных RPC 23.10 (не по датам 16–21.10)
SIZES = (1000, 10000)
fees = json.load(open(FEES))
pc = collections.defaultdict(list)      # монета -> [(chain, fee бп, vol24, dex)]
for k, v in fees.items():
    sym, _, ch = k.partition("@")
    if v["fee"] is not None:
        pc[sym].append((ch, float(v["fee"]) * 100, float(v["vol24"] or 0), v["dex"]))
rows = collections.defaultdict(list)
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.split("\t")
        if len(a) < 24 or a[0] != "R10": continue
        v = [float(x) if x else np.nan for x in a[4:24]]
        rows[coin].append((int(a[1]), float(a[3]), v[2 * 2 + 1], v[2 * 5 + 1], v[2 * 8 + 1]))  # dpx10, dpx60, dpx300 (пары: 0 pre, 1..3 h10, 4..6 h60, 7..9 h300; dpx = 2-й в тройке)
out = []
for coin, ev in sorted(rows.items()):
    ps = pc.get(coin) or pc.get(coin.upper())
    if not ps: continue
    y = np.array([e[2:] for e in ev], float)
    g10, g60, g300 = (np.nanmean(y[:, i]) for i in range(3))
    stg = np.array([e[1] for e in ev]) >= 3
    s300 = float(np.nanmean(y[stg, 2])) if stg.sum() >= 5 else np.nan; ns = int(stg.sum())
    wv = np.array([p[2] for p in ps]) + 1e-9
    fw = float(np.dot([p[1] for p in ps], wv) / wv.sum()); fmin = min(p[1] for p in ps)
    main = ps[int(np.argmax(wv))]
    for sz in SIZES:
        gas = GAS_USD[main[0]] / sz * 1e4
        out.append([coin, main[0], main[3], len(ev), len(ps), round(fmin, 2), round(fw, 2), sz, round(gas, 2),
                    round(g10, 2), round(g60, 2), round(g300, 2),
                    round(g300 - 2 * fw - 2 * gas, 2), round(g300 - 2 * fmin - 2 * gas, 2), round(g300 - fmin - gas, 2),
                    round(g60 - 2 * fmin - 2 * gas, 2), ns, round(s300, 2), round(s300 - 2 * fmin - 2 * gas, 2)])
hdr = ["монета", "сеть_главного_пула", "dex", "событий_R10", "пулов", "fee_min_бп", "fee_взв_бп", "размер_usd", "газ_бп", "gross10", "gross60", "gross300",
       "net_круг300_fee_взв", "net_круг300_fee_min", "net_конец300_fee_min", "net_круг60_fee_min", "сильных_событий", "gross300_сильные", "net_круг300_сильные_fee_min"]
with open(PFX + "-pools.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(hdr); w.writerows(out)
o = np.array([r[12:16] for r in out], float); st3 = np.array([r[18] for r in out], float); sn = np.array([r[16] for r in out], float); fm = np.array([r[5] for r in out]); fe = np.array([r[3] for r in out], float); gm = np.array([r[11] for r in out])
sz = np.array([r[7] for r in out]); ch = np.array([r[1] for r in out])
lines = []
def agg(tag, m):
    if m.sum() == 0: return
    n = int(m.sum())
    lines.append([tag, n, int(fe[m].sum()), round(float(np.median(fm[m])), 1), round(float(fm[m].min()), 1), round(float(np.average(gm[m], weights=fe[m])), 2)] +
                 [round(float(np.average(o[m, j], weights=fe[m])), 2) for j in range(4)] + [round(float((o[m, j] > 0).mean()), 3) for j in (1, 2)])
for s in SIZES:
    m = (sz == s) & (fm < 2) & ~np.isnan(st3)
    if m.sum(): lines.append([f"сильные >=3, fee<2 бп, размер {s}", int(m.sum()), int(sn[m].sum()), 1.0, 1.0, round(float(np.average(gm[m], weights=sn[m])), 2), "", "", "", round(float(np.average(st3[m], weights=sn[m])), 2), round(float((st3[m] > 0).mean()), 3), ""])
    agg(f"все, размер {s}", sz == s)
    for c in ("ethereum", "bsc", "base"): agg(f"{c}, размер {s}", (sz == s) & (ch == c))
    for lo, hi in ((0, 2), (2, 10), (10, 30), (30, 60), (60, 101), (101, 1e9)): agg(f"fee {lo}–{hi} бп, размер {s}", (sz == s) & (fm >= lo) & (fm < hi))
with open(PFX + "-summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["срез", "монет", "событий", "fee_min_медиана_бп", "fee_min_min_бп", "gross300_ср", "net_круг300_fee_взв", "net_круг300_fee_min", "net_конец300_fee_min", "net_круг60_fee_min", "доля_монет_net_круг300>0", "доля_монет_net_конец300>0"]); w.writerows(lines)
for l in lines: print(l)
