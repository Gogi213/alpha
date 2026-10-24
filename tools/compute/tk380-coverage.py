#!/usr/bin/env python3
"""TK-380 шаг 3: охват. GT-свечи (USD-оборот пула 15–22.10) × скачанные свопы (summary.csv SQD) по сетям/монетам."""
import csv, os, sys, json, collections
R = "/data/tk0380"; T0, T1 = 1792022400, 1792713600 + 86400  # 15.10 00:00 .. 23.10 00:00 UTC
pools = list(csv.DictReader(open(f"{R}/pools-all.csv")))
sw = collections.defaultdict(int)
for ch in ("bsc", "base", "ethereum", "arbitrum"):
    p = f"{R}/sqd/{ch}-swaps/summary.csv"
    for r in csv.DictReader(open(p)):
        sw[(ch, r["pool"].lower())] += int(r["swaps"])
# Uniswap v4 (ETH): Swap из PoolManager, ключ — poolId (TK-380 возврат Судьи)
for r in csv.DictReader(open(f"{R}/sqd/ethereum-v4/summary.csv")):
    sw[("ethereum", r["pool"].lower())] += int(r["swaps"])
rows = []
for x in pools:
    ch, a = x["chain"], x["pool"]
    f = f"{R}/gt/out/{ch}/{a}.csv"
    v = n = 0.0
    have = os.path.exists(f)
    if have:
        for r in csv.DictReader(open(f)):
            t = int(r["ts"])
            if T0 <= t < T1:
                v += float(r["vol_usd"]); n += 1
    s = sw.get((ch, a.lower()), 0)
    rows.append(dict(coin=x["coin"], chain=ch, pool=a, dex=x["dex"], vol24=float(x["vol24"]), gt_vol8d=v,
                     gt_min=int(n), gt_file=int(have), swaps=s, swaps_src=int(ch in ("bsc", "base", "ethereum", "arbitrum"))))
out = "/data/tk0380/coverage.csv"
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
tot = sum(r["gt_vol8d"] for r in rows)
agg = collections.defaultdict(lambda: dict(pools=0, coins=set(), vol=0.0, v_sw=0.0, v_gt=0.0, pools_sw=0, sw=0))
for r in rows:
    a = agg[r["chain"]]; a["pools"] += 1; a["coins"].add(r["coin"]); a["vol"] += r["gt_vol8d"]
    if r["gt_file"]: a["v_gt"] += r["gt_vol8d"]
    if r["swaps"] > 0: a["v_sw"] += r["gt_vol8d"]; a["pools_sw"] += 1; a["sw"] += r["swaps"]
res = {}
for ch, a in sorted(agg.items(), key=lambda kv: -kv[1]["vol"]):
    res[ch] = dict(pools=a["pools"], coins=len(a["coins"]), gt_vol8d_musd=round(a["vol"] / 1e6, 1), share=round(a["vol"] / tot, 4),
                   pools_with_swaps=a["pools_sw"], swaps=a["sw"], vol_share_with_swaps=round(a["v_sw"] / a["vol"], 4) if a["vol"] else 0)
# монеты: доля оборота монеты с пулом со свопами
coin = collections.defaultdict(lambda: [0.0, 0.0])
for r in rows:
    coin[r["coin"]][0] += r["gt_vol8d"]
    if r["swaps"] > 0: coin[r["coin"]][1] += r["gt_vol8d"]
cs = sorted(coin.values(), key=lambda z: -z[0])
res["_total"] = dict(gt_vol8d_musd=round(tot / 1e6, 1), coins=len(coin), coins_any_swaps=sum(1 for c in coin.values() if c[1] > 0),
                     vol_share_with_swaps=round(sum(c[1] for c in coin.values()) / tot, 4),
                     coins_ge50pct=sum(1 for c in coin.values() if c[0] > 0 and c[1] / c[0] >= .5))
json.dump(res, open("/data/tk0380/coverage.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(res, indent=1, ensure_ascii=False))
