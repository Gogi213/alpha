import pickle, random, json
d = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb")); df = d["df"]; pairs = d["pairs"]
fixed = ["INIT","SOPH","KAVA","RARE","BARD","BTW","AKE","BMT","BIRB","BTC","ETH","SOL","DOGE","1INCH"]
z5 = df[(df.view=="z")&(df.th==50)&(df.dur>=5000)]
pool = sorted(set(z5.coin) - set(fixed))
random.seed(534); rnd = random.sample(pool, 8)
coins = fixed + rnd
syms = {}
for (c,a,b), p in pairs.items():
    if c in coins:
        sa, sb = p["syms"]; syms.setdefault(a,set()).add(sa); syms.setdefault(b,set()).add(sb)
for c in fixed:
    for ex, s in (("binance", c+"USDT"), ("bybit", c+"USDT"), ("okx", c+"-USDT-SWAP"), ("bingx", c+"-USDT"), ("gate", c+"_USDT")):
        syms.setdefault(ex, set()).add(s)
json.dump({"coins": coins, "syms": {k: sorted(v) for k,v in syms.items()}, "t0": d["t0"], "t1": d["t1"]}, open("/root/rev534/syms.json","w"))
print(coins); print({k: len(v) for k,v in syms.items()})
print("pairs in table for coins:", sum(1 for k in pairs if k[0] in coins))
