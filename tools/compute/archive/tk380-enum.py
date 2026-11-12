"""TK-380: клон tk364-enum.py: порог оборота 30k$ (монеты Gate). Вход - csv от tk380-gate-ticks.py. TK-364 (CEO 03:40): все монеты -> контракты (CoinGecko platforms) -> все DEX-пулы (DexScreener).
Одноимённые токены отсеиваются ценой: цена токена в DexScreener в пределах x0.8..x1.25 от цены Bybit (с учётом префикса 1000).
usage: tk364-enum.py <bybit-ticks.csv> <out-dir> [--known pools2.json,pools3n.json]
Выход: <out>/pools4.json (EVM ethereum/bsc/base, новые пулы, формат pools2) , <out>/enum-all.csv (все найденные пулы всех сетей), <out>/enum-summary.json."""
import sys, csv, json, re, time, urllib.request, collections
tick, out = sys.argv[1], sys.argv[2]
known = set()
if "--known" in sys.argv:
    for f in sys.argv[sys.argv.index("--known") + 1].split(","):
        known |= {v["pool"].lower() for v in json.load(open(f)).values()}
def g(u, t=60):
    for i in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=t))
        except Exception as e: err = e; time.sleep(3 + 5 * i)
    print("FAIL", u[:90], err, file=sys.stderr); return None
# 1. монеты Bybit
coins = {}
for r in csv.DictReader(open(tick, encoding="utf-8-sig"), delimiter=";"):
    s = r["symbol"]; m = re.match(r"^(.*?)(USDT|USDC|PERP)$", s)
    if not m: continue
    b = m.group(1); mult = 1
    mm = re.match(r"^(10+)(?=[A-Z])", b)
    if mm: mult = int(mm.group(1)); b = b[len(mm.group(1)):]
    try: to = float(r["turnover24h_usd"]); px = float(r["price"])
    except: continue
    if to < 30000 or px <= 0 or not b: continue
    c = coins.setdefault(b, {"turn": 0, "px": px / mult, "kinds": set()})
    c["turn"] += to; c["kinds"].add(r["market"])
print("bybit coins", len(coins), flush=True)
# 2. контракты по символу
cg = g("https://api.coingecko.com/api/v3/coins/list?include_platform=true", 180) or []
bysym = collections.defaultdict(list)
for c in cg: bysym[c["symbol"].upper()].append(c)
CH = {"ethereum": "ethereum", "binance-smart-chain": "bsc", "base": "base", "solana": "solana", "arbitrum-one": "arbitrum",
      "polygon-pos": "polygon", "optimistic-ethereum": "optimism", "avalanche": "avalanche", "sui": "sui", "tron": "tron", "ton": "ton"}
cand = collections.defaultdict(set)   # (chain, addr_lower) -> {coin}
for b in coins:
    for c in bysym.get(b, []):
        for p, a in (c.get("platforms") or {}).items():
            if a and p in CH: cand[(CH[p], a if CH[p] in ("solana", "sui", "tron", "ton") else a.lower())].add(b)
print("candidate contracts", len(cand), flush=True)
# 3. DexScreener, пачки по 30 на сеть
bychain = collections.defaultdict(list)
for (ch, a) in cand: bychain[ch].append(a)
rows = []; seen = set()
for ch, addrs in bychain.items():
    for i in range(0, len(addrs), 30):
        d = g(f"https://api.dexscreener.com/tokens/v1/{ch}/" + ",".join(addrs[i:i + 30]))
        time.sleep(0.25)
        for p in d or []:
            bt = p.get("baseToken", {}); a = bt.get("address", "")
            a = a if ch in ("solana", "sui", "tron", "ton") else a.lower()
            cs = cand.get((ch, a))
            if not cs or p["pairAddress"] in seen: continue
            try: px = float(p["priceUsd"]); liq = (p.get("liquidity") or {}).get("usd") or 0; v = (p.get("volume") or {}).get("h24") or 0
            except: continue
            for b in cs:
                if bt.get("symbol", "").upper().lstrip("$") not in (b, "W" + b, "1000" + b) and bt.get("symbol", "").upper() != b: continue
                r0 = px / coins[b]["px"]
                if not (0.8 <= r0 <= 1.25): continue
                seen.add(p["pairAddress"])
                rows.append({"coin": b, "chain": ch, "pool": p["pairAddress"], "dex": p["dexId"], "labels": ",".join(p.get("labels") or []),
                             "quote": p["quoteToken"]["symbol"], "token": a, "vol24": v, "liq": liq, "txns24": sum((p.get("txns") or {}).get("h24", {}).values()) if p.get("txns") else 0,
                             "pxratio": round(r0, 3), "bybit_turn": round(coins[b]["turn"]), "kinds": "+".join(sorted(coins[b]["kinds"]))})
        print(ch, i + 30, "/", len(addrs), "pools", len(rows), flush=True)
rows.sort(key=lambda r: (r["coin"], -r["vol24"]))
with open(out + "/enum-all.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
# 4. EVM-пулы к выгрузке: v2/v3 (адрес 42 знака), <=3 пула на монету и сеть по обороту, новые
EVM = {"ethereum", "bsc", "base"}; DEXES = {"uniswap", "pancakeswap", "aerodrome", "sushiswap"}
pools, per = {}, collections.Counter()
for r in rows:
    if r["chain"] not in EVM or r["dex"] not in DEXES or len(r["pool"]) != 42 or r["pool"].lower() in known or r["vol24"] <= 0: continue
    if per[(r["coin"], r["chain"])] >= 3: continue
    per[(r["coin"], r["chain"])] += 1
    k = per[(r["coin"], r["chain"])]; sym = f'{r["coin"]}-{r["pool"][2:8]}'
    pools[f'{sym}@{r["chain"]}'] = {"sym": sym, "chain": r["chain"], "pool": r["pool"], "dex": r["dex"], "labels": r["labels"],
                                    "quote": r["quote"], "vol24": r["vol24"], "perp": r["coin"] + "USDT", "group": "all"}
json.dump(pools, open(out + "/pools4.json", "w"), indent=1)
S = {"bybit_coins": len(coins), "coins_with_pool_any": len({r["coin"] for r in rows}), "pools_found": len(rows),
     "coins_by_chain": {ch: len({r["coin"] for r in rows if r["chain"] == ch}) for ch in sorted({r["chain"] for r in rows})},
     "pools_by_chain": dict(collections.Counter(r["chain"] for r in rows)), "to_download_new": len(pools),
     "to_download_by_chain": dict(collections.Counter(v["chain"] for v in pools.values())),
     "coins_with_evm_new": len({v["perp"] for v in pools.values()})}
json.dump(S, open(out + "/enum-summary.json", "w"), indent=1); print(json.dumps(S, indent=1))
