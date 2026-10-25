"""TK-393 возврат 2: 139 монет Gate >=30k без цепочки из поддерживаемых + 16 без записи в API: все цепочки Gate (адрес) -> DexScreener по адресу на ЛЮБОЙ сети.
usage: tk393-nochain.py <dir с summary.json, gate-coins.csv>   Выход: nochain-classes.csv, nochain-pools.csv, nochain-summary.json"""
import sys, json, csv, time, urllib.request, urllib.parse, collections
d0 = sys.argv[1]
def g(u, t=60):
    err = None
    for i in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=t))
        except Exception as e:
            err = e
            if getattr(e, "code", 0) in (400, 404): return {"_err": e.code}
            time.sleep(2 + 3 * i)
    return {"_err": str(err)}
S = json.load(open(d0 + "/summary.json"))
coins = {r["coin"]: r for r in csv.DictReader(open(d0 + "/gate-coins.csv", encoding="utf-8"))}
rows = []; cand = collections.defaultdict(set)
for b in S["nochain"] + S["nocur"]:
    d = g(f"https://api.gateio.ws/api/v4/spot/currencies/{urllib.parse.quote(b)}"); time.sleep(0.12)
    chs = d.get("chains") if isinstance(d, dict) else None
    if not chs:
        rows.append({"coin": b, "class": "нет записи в API Gate (/spot/currencies)" + (" код " + str(d.get("_err")) if isinstance(d, dict) and d.get("_err") else ""), "chains": "", "addr": ""}); continue
    withaddr = [(c["name"], (c.get("addr") or "").strip()) for c in chs if (c.get("addr") or "").strip() not in ("", "0x")]
    if withaddr:
        for n, a in withaddr: cand[a.lower() if a.startswith("0x") else a].add(b)
        rows.append({"coin": b, "class": "токен-контракт есть", "chains": ",".join(n for n, _ in withaddr), "addr": ";".join(a for _, a in withaddr)})
    else:
        rows.append({"coin": b, "class": "нативная монета сети (адреса контракта нет)", "chains": ",".join(c["name"] for c in chs), "addr": ""})
addrs = list(cand); pools = []; seen = set()
for i in range(0, len(addrs), 30):
    r = g("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(addrs[i:i + 30])); time.sleep(0.3)
    for p in (r.get("pairs") or []) if isinstance(r, dict) else []:
        a = p.get("baseToken", {}).get("address", ""); a = a.lower() if a.startswith("0x") else a
        cs = cand.get(a)
        if not cs or p["pairAddress"] in seen: continue
        try: px = float(p["priceUsd"]); v = (p.get("volume") or {}).get("h24") or 0; liq = (p.get("liquidity") or {}).get("usd") or 0
        except: continue
        for b in cs:
            r0 = px / float(coins[b]["px"])
            if not (0.8 <= r0 <= 1.25): continue
            seen.add(p["pairAddress"]); pools.append({"coin": b, "chain": p["chainId"], "pool": p["pairAddress"], "dex": p["dexId"], "token": a, "vol24": v, "liq": liq, "pxratio": round(r0, 3)})
by = {}
for p in pools: by.setdefault(p["coin"], []).append(p)
for r in rows:
    ps = by.get(r["coin"], [])
    r["pools"] = len(ps); r["pool_chains"] = ",".join(sorted({p["chain"] for p in ps})); r["vol24"] = round(sum(p["vol24"] for p in ps))
    if r["class"] == "токен-контракт есть" and not ps: r["class"] = "токен-контракт есть, DexScreener пулов не отдаёт (цена не сошлась или нет пула)"
with open(d0 + "/nochain-classes.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
if pools:
    with open(d0 + "/nochain-pools.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(pools[0])); w.writeheader(); w.writerows(sorted(pools, key=lambda r: -r["vol24"]))
print(collections.Counter(r["class"] for r in rows)); print("pools", len(pools), "coins", len(by), collections.Counter(p["chain"] for p in pools)); print({c: round(sum(p["vol24"] for p in pools if p["chain"] == c)) for c in {p["chain"] for p in pools}})
