#!/usr/bin/env python3
"""TK-383 (возврат): история фандинга OKX USDT-swap с 2026-09-01 UTC -> data/tk383/funding-okx.csv, lists-okx.json (только монеты, что есть на других биржах)."""
import csv, json, re, time
from concurrent.futures import ThreadPoolExecutor
import requests
S = requests.Session()
START = 1788220800000  # 2026-09-01 00:00 UTC
D = "data/tk383"
def get(u, p):
    for i in range(4):
        try:
            r = S.get(u, params=p, timeout=20)
            if r.status_code == 200: return r.json()
            time.sleep(1 + 2 * i)
        except Exception: time.sleep(1 + i)
def coin_of(b): return re.sub(r"^(1000000|10000|1000|1M)", "", b.upper())
L = json.load(open(f"{D}/lists.json"))
other = {c for ex in L for c in L[ex].values()}
ins = get("https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"})["data"]
okx = {i["instId"]: coin_of(i["instId"].split("-")[0]) for i in ins if i["instId"].endswith("-USDT-SWAP") and i["state"] == "live" and coin_of(i["instId"].split("-")[0]) in other}
json.dump(okx, open(f"{D}/lists-okx.json", "w"))
print("okx монет:", len(okx), flush=True)
def hist(s):
    rows, after = [], None
    for _ in range(10):
        p = {"instId": s, "limit": 100}
        if after: p["after"] = after
        j = get("https://www.okx.com/api/v5/public/funding-rate-history", p)
        l = (j or {}).get("data") or []
        if not l: break
        rows += [(int(x["fundingTime"]), float(x["realizedRate"] or x["fundingRate"])) for x in l]
        after = min(int(x["fundingTime"]) for x in l)
        if after <= START or len(l) < 100: break
    return s, [(t, r) for t, r in rows if t >= START]
with open(f"{D}/funding-okx.csv", "w", newline="") as f, ThreadPoolExecutor(8) as p:
    w = csv.writer(f); w.writerow(["ex", "coin", "sym", "t_ms", "rate"])
    for s, rows in p.map(hist, list(okx)):
        for t, r in sorted(set(rows)): w.writerow(["okx", okx[s], s, t, r])
print("done")
