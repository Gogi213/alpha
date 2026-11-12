#!/usr/bin/env python3
"""TK-366: оценка объёма закачки ДО скачивания. HEAD (Content-Length) по выборке монет×суток, экстраполяция по рангу оборота.
  tk366-size.py <data/tk366> [--step N]    читает fut|spot-coins.csv и *-todo.txt, пишет size-sample-*.csv и size-estimate.json"""
import csv, sys, json, subprocess, statistics as st
from concurrent.futures import ThreadPoolExecutor
d = sys.argv[1]; step = int(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else 12
DAYS = ["2026-07-15", "2026-08-15", "2026-09-15"]
URL = {("fut", "ob"): "https://quote-saver.bycsi.com/orderbook/linear/{s}/{d}_{s}_ob200.data.zip",
       ("fut", "tr"): "https://public.bybit.com/trading/{s}/{s}{d}.csv.gz",
       ("spot", "ob"): "https://quote-saver.bycsi.com/orderbook/spot/{s}/{d}_{s}_ob200.data.zip",
       ("spot", "tr"): "https://public.bybit.com/spot/{s}/{s}_{d}.csv.gz"}
def head(a):
    mk, kind, s, day = a
    r = subprocess.run(["curl", "-sI", "--max-time", "30", URL[(mk, kind)].format(s=s, d=day)], capture_output=True, text=True)
    code, n = 0, 0
    for ln in r.stdout.splitlines():
        if ln.startswith("HTTP"): code = int(ln.split()[1])
        if ln.lower().startswith("content-length"): n = int(ln.split(":")[1])
    return mk, kind, s, day, code, n
est = {}
for mk in ("fut", "spot"):
    coins = list(csv.DictReader(open(f"{d}/{mk}-coins.csv")))
    rank = {c["symbol"]: i for i, c in enumerate(coins)}
    todo = [l.split() for l in open(f"{d}/{mk}-todo.txt")]
    samp = [c["symbol"] for c in coins[::step]]
    jobs = [(mk, k, s, day) for s in samp for day in DAYS for k in ("ob", "tr")]
    with ThreadPoolExecutor(8) as ex: res = list(ex.map(head, jobs))
    with open(f"{d}/size-sample-{mk}.csv", "w") as f:
        f.write("market,kind,symbol,day,http,bytes\n"); [f.write(",".join(map(str, r)) + "\n") for r in res]
    # по скользящему окну рангов: средний размер файла (ob+tr) на сутки, доля 404
    n = len(coins); W = max(step * 3, 30)
    by = {}
    for mk_, k, s, day, code, nb in res: by.setdefault(s, []).append((k, code, nb))
    def local(i):
        ss = [s for s in samp if abs(rank[s] - i) <= W]
        ok = [sum(nb for k, c, nb in by[s] if c == 200) for s in ss]
        tot = [len(by[s]) for s in ss]; hit = [sum(1 for k, c, nb in by[s] if c == 200) for s in ss]
        return (sum(ok) / max(1, sum(hit)), sum(hit) / max(1, sum(tot)))
    ob_sz = {}
    total = 0.0; days_ok = 0.0
    cache = {}
    for s, day in todo:
        i = rank[s]; b = i // W
        if b not in cache: cache[b] = local(b * W + W // 2)
        avg_file, p = cache[b]
        total += 2 * avg_file * p; days_ok += p      # ob+tr на монето-сутки
    est[mk] = {"coins": n, "todo_coin_days": len(todo), "sampled_coins": len(samp), "head_ok_share": sum(1 for r in res if r[4] == 200) / len(res),
               "est_coin_days_with_files": round(days_ok), "est_bytes_gb": round(total / 1e9, 1),
               "median_ob_mb": round(st.median([r[5] for r in res if r[1] == "ob" and r[4] == 200] or [0]) / 1e6, 2),
               "median_tr_mb": round(st.median([r[5] for r in res if r[1] == "tr" and r[4] == 200] or [0]) / 1e6, 2)}
    print(mk, est[mk])
json.dump(est, open(f"{d}/size-estimate.json", "w"), ensure_ascii=False, indent=1)
