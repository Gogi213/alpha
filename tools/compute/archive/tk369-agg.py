#!/usr/bin/env python3
"""TK-369 / P-25: агрегация вывода tk369-run.py (по монето-суткам) -> бутстрап, BH, вердикт §6.
usage: tk369-agg.py <dir с coin/*.json> <bank.csv> <out-prefix>   (numpy; бутстрап 2000 повторов по монето-суткам)
Правило (§6): Snet(t0=ts+1) нижняя граница > 0 и BH q=0.05 хотя бы при одном h; знак в >=5 из 6 суток и >=8 монет (все, если < 8);
Sex3 (без топ-3 монет по n нижняя граница > 0, доля топ-3 <= 0.6); доза-отклик (Спирмен по бинам >= 0.5); p_rand < 0.05 (аналитически: знаковая
рандомизация, sd из базового 2-го момента монето-суток) и контроль 1 того же знака (Snet уже = M - сдвиг)."""
import sys, os, json, glob, csv, math
import numpy as np

D, BANK, PFX = sys.argv[1:4]
H = [1, 5, 30, 60, 300, 900, 3600]
rng = np.random.default_rng(369)
NB = 2000

info = {r["id"]: r for r in csv.DictReader(open(BANK, encoding="utf-8"))}
cells = {}        # (hid,v,hi) -> list of (coin, day, n0, sumM, n1, sumS, sumS2)
bins = {}         # (hid,v,hi,b) -> [n, sumS]
base = {}         # (coin, day, hi) -> [n, sum M^2]
coins_all = []
for f in sorted(glob.glob(f"{D}/coin/*.json")):
    coin = os.path.basename(f)[:-5]
    r = json.load(open(f))
    if "acc" not in r: continue
    coins_all.append(coin)
    for hid, v, hi, day, n0, sm, n1, ss, ss2 in r["acc"]:
        cells.setdefault((hid, v, hi), []).append((coin, day, n0, sm, n1, ss, ss2))
    for hid, v, hi, b, n, ss in r["bins"]:
        x = bins.setdefault((hid, v, hi, b), [0, 0.0]); x[0] += n; x[1] += ss
    for d, hi, n, s2 in r["base"]:
        base[(coin, d, hi)] = [n, s2]

def ncdf(z): return 0.5 * math.erfc(-z / math.sqrt(2))

def boot(n1, sS):
    K = len(n1)
    if K < 5 or n1.sum() == 0: return None
    idx = rng.integers(0, K, (NB, K))
    den = n1[idx].sum(1); num = sS[idx].sum(1)
    ok = den > 0
    m = num[ok] / den[ok]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float((m <= 0).mean())

def spearman(xs, ys):
    if len(xs) < 3: return float("nan")
    rx = np.argsort(np.argsort(xs)); ry = np.argsort(np.argsort(ys))
    if ry.std() == 0: return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])

rows = []
for (hid, v, hi), lst in sorted(cells.items()):
    a = np.array([(x[2], x[3], x[4], x[5], x[6]) for x in lst], dtype=float)
    coin_arr = np.array([x[0] for x in lst]); day_arr = np.array([x[1] for x in lst])
    n0, sM, n1, sS, sS2 = a.T
    N0, N1 = n0.sum(), n1.sum()
    if N1 == 0: continue
    meanS = sS.sum() / N1; meanM = sM.sum() / max(N0, 1)
    b = boot(n1, sS)
    if b is None: continue
    lo, hi_, p1 = b
    # дни
    dpos = 0; dtot = 0
    for d in range(16, 22):
        mk = day_arr == d
        if n1[mk].sum() > 0:
            dtot += 1; dpos += (sS[mk].sum() / n1[mk].sum()) > 0
    # монеты
    cs = {}
    for c, nn, ss in zip(coin_arr, n1, sS):
        x = cs.setdefault(c, [0, 0.0]); x[0] += nn; x[1] += ss
    cpos = sum(1 for c, (nn, ss) in cs.items() if nn > 0 and ss > 0); ncoin = sum(1 for c, (nn, ss) in cs.items() if nn > 0)
    top3 = sorted(cs, key=lambda c: -cs[c][0])[:3]
    share3 = sum(cs[c][0] for c in top3) / N1
    mk = ~np.isin(coin_arr, top3)
    b3 = boot(n1[mk], sS[mk]) if mk.sum() >= 5 else None
    lo3 = b3[0] if b3 else float("nan")
    # доза-отклик
    bb = [(bi, bins[(hid, v, hi, bi)]) for bi in range(6) if (hid, v, hi, bi) in bins and bins[(hid, v, hi, bi)][0] >= 30]
    sp = spearman([x[0] for x in bb], [x[1][1] / x[1][0] for x in bb]) if len(bb) >= 3 else float("nan")
    # p_rand (аналитически, по M при t0=ts+1)
    var = 0.0
    for c, d, nn0, smm in zip(coin_arr, day_arr, n0, sM):
        bs = base.get((c, d, hi))
        if bs and bs[0] > 0: var += nn0 * (bs[1] / bs[0])
    sd = math.sqrt(var) / N0 if N0 > 0 and var > 0 else float("nan")
    prand = 1 - ncdf(meanM / sd) if sd == sd and sd > 0 else float("nan")
    rows.append(dict(hid=hid, v=v, h=H[hi], n=int(N1), cd=len(lst), coins=ncoin, meanM=meanM, snet=meanS, lo=lo, hi=hi_, p=p1,
                     days_pos=f"{dpos}/{dtot}", coins_pos=f"{cpos}/{ncoin}", top3_share=share3, lo_ex3=lo3, spearman=sp, p_rand=prand))

# BH по всем посчитанным клеткам (оба опорных момента)
ps = np.array([r["p"] for r in rows]); m = len(ps)
order = np.argsort(ps); q = np.empty(m)
prev = 1.0
for rank, i in zip(range(m, 0, -1), order[::-1]):
    prev = min(prev, ps[i] * m / rank); q[i] = prev
for r, qq in zip(rows, q): r["q_bh"] = float(qq)
for r in rows:
    need_coins = min(8, int(r["coins_pos"].split("/")[1]))
    dp, dt = map(int, r["days_pos"].split("/")); cp = int(r["coins_pos"].split("/")[0])
    c1 = r["v"] == 1 and r["lo"] > 0 and r["q_bh"] < 0.05
    c2 = dt >= 5 and dp >= min(5, dt) and cp >= need_coins
    c3 = r["lo_ex3"] == r["lo_ex3"] and r["lo_ex3"] > 0 and r["top3_share"] <= 0.6
    c4 = r["spearman"] == r["spearman"] and r["spearman"] >= 0.5
    c5 = r["p_rand"] == r["p_rand"] and r["p_rand"] < 0.05
    r["c1_ci_bh"], r["c2_days_coins"], r["c3_ex3"], r["c4_dose"], r["c5_rand"] = c1, c2, c3, c4, c5
    r["like_alpha"] = bool(c1 and c2 and c3 and c4 and c5)
    r["above_costs"] = bool(r["like_alpha"] and r["snet"] >= 11)
    r["family"] = info.get(r["hid"].replace(r["hid"][1:4], r["hid"][1:4]), {}).get("family", r["hid"][0])

# id в банке — G16-A001: подставить
bank_ids = {k.split("-")[1]: k for k in info}
cols = ["hid", "v", "h", "n", "cd", "coins", "meanM", "snet", "lo", "hi", "p", "q_bh", "days_pos", "coins_pos", "top3_share", "lo_ex3", "spearman", "p_rand",
        "c1_ci_bh", "c2_days_coins", "c3_ex3", "c4_dose", "c5_rand", "like_alpha", "above_costs"]
with open(PFX + ".csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["id"] + cols[1:])
    for r in rows:
        w.writerow([bank_ids.get(r["hid"], r["hid"])] + [(f"{r[c]:.4g}" if isinstance(r[c], float) else r[c]) for c in cols[1:]])
res = {"m": m, "cells": len(rows), "coins": len(coins_all), "like_alpha": sorted({r["hid"] for r in rows if r["like_alpha"]}),
       "sig_q05": sorted({r["hid"] for r in rows if r["v"] == 1 and r["q_bh"] < 0.05 and r["lo"] > 0})}
print(json.dumps(res, ensure_ascii=False))
top = sorted([r for r in rows if r["v"] == 1], key=lambda r: -r["snet"] if r["lo"] > 0 else 1e9 + r["p"])[:25]
with open(PFX + "-top.txt", "w", encoding="utf-8") as f:
    f.write(f"m={m} клеток, монет {len(coins_all)}\n")
    for r in top:
        f.write(f"{r['hid']} h={r['h']:>4} n={r['n']:>7} монето-суток={r['cd']:>4} монет={r['coins']:>3} Snet={r['snet']:+.2f} [{r['lo']:+.2f};{r['hi']:+.2f}] q={r['q_bh']:.3f} "
                f"дни {r['days_pos']} монеты {r['coins_pos']} top3={r['top3_share']:.2f} ex3_lo={r['lo_ex3']:+.2f} доза={r['spearman']:+.2f} p_rand={r['p_rand']:.3f} "
                f"alpha={r['like_alpha']}\n")
print(open(PFX + "-top.txt", encoding="utf-8").read())
