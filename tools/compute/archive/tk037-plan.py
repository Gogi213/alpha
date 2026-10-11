#!/usr/bin/env python3
"""TK-037: топ-100 по суткам из turnover-daily.csv → монеты «хоть раз в топ-100», недостающие монето-сутки, объём по HEAD.
  tk037-plan.py <dir>   читает <dir>/turnover-daily.csv, пишет ever-top100.csv, missing-days.csv, sizes.csv"""
import csv, os, re, sys, glob, collections, urllib.request, concurrent.futures as cf
D = sys.argv[1]; ALPHA = "/data/alpha"
by = collections.defaultdict(dict)
for r in csv.DictReader(open(f"{D}/turnover-daily.csv")):
    by[r["day"]][r["symbol"]] = float(r["turnover_usdt"])
ever = collections.defaultdict(lambda: [0, 0.0])  # sym -> [days in top100, max rank-best]
best = {}
for day, m in by.items():
    for i, (s, t) in enumerate(sorted(m.items(), key=lambda x: -x[1])[:100], 1):
        ever[s][0] += 1; best[s] = min(best.get(s, 999), i)
have = set()
for p in glob.glob(f"{ALPHA}/epochs/*/*.binlog") + glob.glob(f"{ALPHA}/root/*.binlog"):
    m = re.match(r"(.+USDT)-(\d{4}-\d\d-\d\d)(?:-p\d+)?\.binlog$", os.path.basename(p))
    if m: have.add((m[1], m[2]))
hs = {s for s, _ in have}
print("days", len(by), "ever-top100 coins", len(ever), "already have coins", len(hs), "ever∩have", len(set(ever) & hs))
with open(f"{D}/ever-top100.csv", "w") as f:
    w = csv.writer(f); w.writerow(["symbol", "days_in_top100", "best_rank", "have_days"])
    for s in sorted(ever): w.writerow([s, ever[s][0], best[s], sum(1 for h in have if h[0] == s)])
miss = sorted((s, d) for d in by for s in by[d] if s in ever and (s, d) not in have and by[d][s] > 0)
with open(f"{D}/missing-days.csv", "w") as f:
    w = csv.writer(f); w.writerow(["symbol", "day"]); w.writerows(miss)
print("missing coin-days", len(miss))
def head(u):
    for _ in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=30)
            return int(r.headers.get("Content-Length", -1))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404): return -1
        except Exception: pass
    return -2
def one(sd):
    s, d = sd
    return s, d, head(f"https://quote-saver.bycsi.com/orderbook/linear/{s}/{d}_{s}_ob200.data.zip"), head(f"https://public.bybit.com/trading/{s}/{s}{d}.csv.gz")
with cf.ThreadPoolExecutor(16) as ex, open(f"{D}/sizes.csv", "w") as f:
    w = csv.writer(f); w.writerow(["symbol", "day", "ob_bytes", "trades_gz_bytes"])
    for r in ex.map(one, miss): w.writerow(r)
