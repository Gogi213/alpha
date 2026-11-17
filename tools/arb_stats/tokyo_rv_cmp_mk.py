import pickle
from collections import defaultdict
for k in ("bts", "bcts2"):
    try:
        d = pickle.load(open(f"/data/arb-tokyo-review/mk-{k}.pkl", "rb"))
    except FileNotFoundError:
        continue
    tot = defaultdict(float)
    for key, st in d["rows"].items():
        if key[0] != "mk" or "bingx" in (key[2], key[3]):
            continue
        th, lat, ex_, dm = key[4]
        tot[(th, ex_, dm, lat, "all")] += st["usd_cap"]
        tot[(th, ex_, dm, lat, f"{key[2]}>{key[3]}")] += st["usd_cap"]
    for th in (20.0, 30.0):
        for dm in ("L", "300"):
            for pe in ("all", "binance>bybit", "bybit>binance", "binance>okx", "okx>binance", "bybit>okx", "okx>bybit"):
                print(f"mk-{k} θ={th/100:g} x1-t D={dm} {pe}: " + " | ".join(f"L{l} {tot.get((th, 'x1-t', dm, l, pe), 0):+.0f}" for l in (1, 2, 5, 10)))
