"""TK-364: пулы второй выгрузки — самый оборотистый пул токена на сети из DexScreener (23.10). usage: tk364-pools2.py <out.json>"""
import sys, json, urllib.request, time
CH = {"ethereum": "ethereum", "bsc": "bsc", "base": "base"}
TOK = {  # символ: [(сеть, контракт)]; хайкапы v4 (has_pool) + мажоры-калибровка
 "LINK": [("ethereum", "0x514910771af9ca656af840dff83e8264ecf986ca")],
 "UNI": [("ethereum", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984")],
 "SHIB": [("ethereum", "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce")],
 "QNT": [("ethereum", "0x4a220e6096b25eadb88358cb44068a3248254675")],
 "ENA": [("ethereum", "0x57e114b691db790c35207b2e685d4a43181e6061")],
 "AAVE": [("ethereum", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9")],
 "ONDO": [("ethereum", "0xfaba6f8e4a5e8ab82f62fe7c39859fa577269be3")],
 "WLD": [("ethereum", "0x163f8c2467924be0ae7b5347228cabf260318753")],
 "WLFI": [("ethereum", "0xda5e1988097297dcdc1f90d4dfe7909e847cbef6")],
 "SKY": [("ethereum", "0x56072c95faa701256059aa122697b133aded9279")],
 "PEPE": [("ethereum", "0x6982508145454ce325ddbe47a25d4ec3d2311933")],
 "MNT": [("ethereum", "0x3c3a81e81dc49a522a592e7622a7e711c06bf354")],
 "ICP": [("ethereum", "0x00f3c42833c3170159af4e92dbb451fb3f708917")],
 "CRO": [("ethereum", "0xa0b73e1ff0b80914ab6fe0444e65848c4c34450b")],
 "XAUT": [("ethereum", "0x68749665ff8d2d112fa859aa293f07a622782f38")],
 "PAXG": [("ethereum", "0x45804880de22913dafe09f4980848ece6ecbaf78")],
 "GRAM": [("ethereum", "0x582d872a1b094fc48f5de31d3b73f2d9be47def1")],
 "ASTER": [("bsc", "0x000ae314e2a2172a039b26378814c252734f556a")],
 "MORPHO": [("base", "0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842")],
 "VVV": [("base", "0xacfe6019ed1a7dc6f7b508c02d1b04ec88cc21bf")],
 # мажоры: ETH — WETH, BTC — WBTC (ETH) и BTCB (BSC), SOL/BNB — обёртки на BSC
 "ETH": [("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")],
 "BTC": [("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"), ("bsc", "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c")],
 "SOL": [("bsc", "0x570a5d26f7765ecb712c0924e4de545b89fd43df")],
 "BNB": [("bsc", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")],
}
res = {}
for sym, lst in TOK.items():
    for chain, addr in lst:
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(
                "https://api.dexscreener.com/latest/dex/tokens/" + addr, headers={"user-agent": "Mozilla/5.0"}), timeout=30))
        except Exception as e:
            print(sym, chain, "ERR", e); continue
        ps = [p for p in (r.get("pairs") or []) if p.get("chainId") == chain and p.get("dexId") in
              ("uniswap", "pancakeswap", "aerodrome", "sushiswap") and p["baseToken"]["address"].lower() == addr]
        ps.sort(key=lambda p: -(p.get("volume", {}).get("h24") or 0))
        if ps:
            p = ps[0]
            res[f"{sym}@{chain}"] = {"sym": sym, "chain": chain, "pool": p["pairAddress"], "dex": p["dexId"], "labels": p.get("labels"),
                                     "quote": p["quoteToken"]["symbol"], "vol24": p["volume"]["h24"], "txns24": p["txns"]["h24"]}
            print(sym, chain, p["dexId"], p.get("labels"), p["quoteToken"]["symbol"], p["volume"]["h24"], p["txns"]["h24"], p["pairAddress"])
        else:
            print(sym, chain, "no pool")
        time.sleep(0.4)
json.dump(res, open(sys.argv[1], "w"), indent=1)
