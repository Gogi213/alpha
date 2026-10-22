"""TK-364: пулы всех монет юниверса (v3 mid+high, EVM-сети) сверх pools2 — самый оборотистый пул токена на сети из DexScreener.
usage: tk364-pools3.py <out.json> [--chains bsc,ethereum,base]"""
import sys, json, csv, urllib.request, time
out = sys.argv[1]
chains = sys.argv[sys.argv.index("--chains") + 1].split(",") if "--chains" in sys.argv else ["bsc", "ethereum", "base"]
F = "docs/findings/"
rows = []
for f in ("tk364-universe-v3-2026-10-23.csv", "tk364-universe-v4-high-2026-10-23.csv"):
    rows += list(csv.DictReader(open(F + f, encoding="utf-8")))
known = json.load(open(F + "tk364-pools2.json")) if len(sys.argv) > 3 and False else {}
res, seen = {}, set()
for r in rows:
    ch, tok, sym = r.get("dex_chain", ""), r.get("token", ""), r.get("base") or r.get("symbol")
    if ch not in chains or not tok.startswith("0x") or (sym, ch) in seen: continue
    seen.add((sym, ch))
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://api.dexscreener.com/latest/dex/tokens/" + tok, headers={"user-agent": "Mozilla/5.0"}), timeout=30))
    except Exception as e:
        print(sym, ch, "ERR", e); time.sleep(2); continue
    ps = [p for p in (d.get("pairs") or []) if p.get("chainId") == ch and p.get("dexId") in
          ("uniswap", "pancakeswap", "aerodrome", "sushiswap") and p["baseToken"]["address"].lower() == tok.lower()
          and len(p["pairAddress"]) <= 42]
    ps.sort(key=lambda p: -(p.get("volume", {}).get("h24") or 0))
    if ps:
        p = ps[0]
        res[f"{sym}@{ch}"] = {"sym": sym, "chain": ch, "pool": p["pairAddress"], "dex": p["dexId"], "labels": p.get("labels"),
                              "quote": p["quoteToken"]["symbol"], "vol24": p["volume"]["h24"], "txns24": p["txns"]["h24"],
                              "perp": r.get("symbol"), "group": r.get("group")}
        print(sym, ch, p["dexId"], p["volume"]["h24"])
    else:
        print(sym, ch, "no pool")
    time.sleep(0.35)
json.dump(res, open(out, "w"), indent=1)
print("pools", len(res), "of", len(seen))
