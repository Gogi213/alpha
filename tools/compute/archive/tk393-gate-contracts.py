"""TK-393: контракты монет Gate >=30k$ из самого Gate (GET /spot/currencies/{ccy}, chains+addr) -> DexScreener по адресу -> пулы; дельта к tk380-pools.
usage: tk393-gate-contracts.py <out-dir>   Выход: gate-coins.csv, gate-contracts.csv, pools-new.csv, summary.json"""
import sys, json, re, csv, time, urllib.request, collections
out = sys.argv[1]
def g(u, t=60):
    err = None
    for i in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=t))
        except Exception as e:
            err = e
            if getattr(e, "code", 0) in (400, 404): return None
            time.sleep(2 + 3 * i)
    print("FAIL", u[:90], err, file=sys.stderr); return None
spot = g("https://api.gateio.ws/api/v4/spot/tickers"); fut = g("https://api.gateio.ws/api/v4/futures/usdt/tickers")
coins = {}
def add(b, turn, px, kind):
    if re.search(r"(3L|3S|5L|5S)$", b): return
    c = coins.setdefault(b, {"turn": 0.0, "px": float(px or 0), "kinds": set()}); c["turn"] += turn; c["kinds"].add(kind)
for t in spot:
    m = re.match(r"^(.+)_USDT$", t["currency_pair"])
    if m: add(m.group(1), float(t["quote_volume"] or 0), t["last"], "спот")
for t in fut:
    m = re.match(r"^(.+)_USDT$", t["contract"])
    if m: add(m.group(1), float(t["volume_24h_quote"] or 0), t["last"], "фьюч")
coins = {b: c for b, c in coins.items() if c["turn"] >= 30000 and c["px"] > 0}
print("gate coins >=30k", len(coins), flush=True)
with open(out + "/gate-coins.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["coin", "turn24", "px", "kinds"])
    for b, c in sorted(coins.items()): w.writerow([b, round(c["turn"]), c["px"], "+".join(sorted(c["kinds"]))])
CH = {"ETH": "ethereum", "BSC": "bsc", "BASE": "base", "SOL": "solana", "ARBEVM": "arbitrum", "MATIC": "polygon", "POLYGON": "polygon", "OPETH": "optimism",
      "AVAX_C": "avalanche", "AVAXC": "avalanche", "SUI": "sui", "TRX": "tron", "TON": "ton", "BASEEVM": "base", "ARB": "arbitrum", "OP": "optimism"}
rows = []; nocur = []; nochain = []; seenchains = collections.Counter()
for i, b in enumerate(sorted(coins)):
    d = g(f"https://api.gateio.ws/api/v4/spot/currencies/{b}"); time.sleep(0.12)
    if not d or not d.get("chains"): nocur.append(b); continue
    got = False
    for c in d["chains"]:
        seenchains[c["name"]] += 1
        ch = CH.get(c["name"].upper()); a = (c.get("addr") or "").strip()
        if ch and a and a.lower() not in ("", "0x"): rows.append((b, ch, a if ch in ("solana", "sui", "tron", "ton") else a.lower(), c["name"])); got = True
    if not got: nochain.append(b)
    if i % 100 == 0: print(i, len(rows), flush=True)
with open(out + "/gate-contracts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["coin", "chain", "addr", "gate_chain"]); w.writerows(rows)
print("contracts", len(rows), "coins", len({r[0] for r in rows}), "nocur", len(nocur), "nochain", len(nochain), flush=True)
cand = collections.defaultdict(set)
for b, ch, a, _ in rows: cand[(ch, a)].add(b)
bychain = collections.defaultdict(list)
for ch, a in cand: bychain[ch].append(a)
pools = []; seen = set()
for ch, addrs in bychain.items():
    if ch in ("sui", "ton"): continue
    for i in range(0, len(addrs), 30):
        d = g(f"https://api.dexscreener.com/tokens/v1/{ch}/" + ",".join(addrs[i:i + 30])); time.sleep(0.25)
        for p in d or []:
            bt = p.get("baseToken", {}); a = bt.get("address", ""); a = a if ch in ("solana", "tron") else a.lower()
            cs = cand.get((ch, a))
            if not cs or p["pairAddress"] in seen: continue
            try: px = float(p["priceUsd"]); liq = (p.get("liquidity") or {}).get("usd") or 0; v = (p.get("volume") or {}).get("h24") or 0
            except: continue
            for b in cs:
                r0 = px / coins[b]["px"]
                mult = 1
                if not (0.8 <= r0 <= 1.25): continue
                seen.add(p["pairAddress"])
                pools.append({"coin": b, "chain": ch, "pool": p["pairAddress"], "dex": p["dexId"], "labels": ",".join(p.get("labels") or []), "quote": p["quoteToken"]["symbol"],
                              "token": a, "vol24": v, "liq": liq, "pxratio": round(r0, 3), "kinds": "+".join(sorted(coins[b]["kinds"]))})
        print(ch, i + 30, "/", len(addrs), "pools", len(pools), flush=True)
known = {r["pool"].lower() for r in csv.DictReader(open("docs/findings/tk380-pools-2026-10-23.csv", encoding="utf-8"))}
known_coins = {r["coin"] for r in csv.DictReader(open("docs/findings/tk380-pools-2026-10-23.csv", encoding="utf-8"))}
new = [p for p in pools if p["pool"].lower() not in known]
if new:
    with open(out + "/pools-new.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(new[0])); w.writeheader(); w.writerows(sorted(new, key=lambda r: -r["vol24"]))
S = {"gate_coins": len(coins), "currencies_no_entry": len(nocur), "no_supported_chain_addr": len(nochain), "contracts": len(rows),
     "coins_with_contract": len({r[0] for r in rows}), "pools_found": len(pools), "coins_with_pool": len({p["coin"] for p in pools}),
     "pools_new_vs_tk380": len(new), "coins_new_vs_tk380": len({p["coin"] for p in new} - known_coins),
     "new_by_chain": dict(collections.Counter(p["chain"] for p in new)), "new_vol24_by_chain": {c: round(sum(p["vol24"] for p in new if p["chain"] == c)) for c in {p["chain"] for p in new}},
     "gate_chain_names": dict(seenchains.most_common()), "nocur": nocur, "nochain": nochain}
json.dump(S, open(out + "/summary.json", "w"), indent=1, ensure_ascii=False); print(json.dumps({k: v for k, v in S.items() if k not in ("nocur", "nochain")}, ensure_ascii=False))
