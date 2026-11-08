#!/usr/bin/env python3
"""TK-500: список пар для orch.py из списков контрактов G0 (TK-496 symbols.json) и метаданных бирж.
  mkpairs.py --fetch meta.json                                   — выкачать ctVal*ctMult (OKX) и quanto_multiplier (Gate) из публичного REST
  mkpairs.py --symbols symbols.json --binance bin.txt --meta meta.json --out pairs.csv [--exchanges binance,okx,bitget,aster,bybit,gate] [--min-ex 2]
Актив = основание тикера (без USDT/-USDT-SWAP/_USDT и без множителя 1000/10000/1000000/1M, у Hyperliquid без префикса k): масштаб цены между биржами в d сокращается уровнем P̄_L, размер заявки
считается в монетах (lot = N/(mid*ct), orch.py). Пара = любые две биржи из --exchanges с активом, порядок A,B — по порядку списка --exchanges. Выход: pairs.csv (asset,pair — числа для assemble.py;
lot пусто = авто), assets.csv (asset,base). Одно основание дважды на бирже (BONK и 1000BONK) -> актив на этой бирже пропущен, в stderr. Совпадение тикера != тот же актив: сверка — G0 (mid/mark)."""
import argparse, csv, itertools, json, re, sys, urllib.request

SYMKEY = {"okx": "okx_futures", "bitget": "bitget_futures", "aster": "aster_futures", "hyperliquid": "hyperliquid_futures", "bybit": "bybit", "gate": "gate"}


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "p40"}), timeout=30))


def fetch(path):
    okx = {d["instId"]: float(d["ctVal"]) * float(d["ctMult"]) for d in get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")["data"] if d["ctType"] == "linear"}
    gate = {d["name"]: float(d["quanto_multiplier"]) for d in get("https://api.gateio.ws/api/v4/futures/usdt/contracts")}
    json.dump({"okx": okx, "gate": gate}, open(path, "w"), indent=0)
    print("okx", len(okx), "gate", len(gate))


def base(ex, s):
    if ex == "okx":
        if not s.endswith("-USDT-SWAP"):
            return None
        s = s[: -len("-USDT-SWAP")]
    elif ex == "gate":
        if not s.endswith("_USDT"):
            return None
        s = s[: -len("_USDT")]
    elif ex == "hyperliquid":
        if len(s) > 1 and s[0] == "k" and s[1:].isupper():
            s = s[1:]
    else:
        if not s.endswith("USDT"):
            return None
        s = s[:-4]
    return re.sub(r"^(1000000|10000|1000|1M)(?=[A-Z])", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch"), ap.add_argument("--symbols"), ap.add_argument("--binance"), ap.add_argument("--meta"), ap.add_argument("--out")
    ap.add_argument("--exchanges", default="binance,okx,bitget,aster,bybit,gate")
    ap.add_argument("--min-ex", type=int, default=2)
    a = ap.parse_args()
    if a.fetch:
        return fetch(a.fetch)
    exs = a.exchanges.split(",")
    syms = json.load(open(a.symbols))
    meta = json.load(open(a.meta))
    lists = {"binance": [x.strip() for x in open(a.binance) if x.strip()] if a.binance else []}
    lists.update({e: syms[SYMKEY[e]] for e in exs if e in SYMKEY})
    by = {}
    for e in exs:
        seen = {}
        for s in lists.get(e, []):
            b = base(e, s)
            if b:
                seen.setdefault(b, []).append(s)
        for b, ss in seen.items():
            if len(ss) > 1:
                print(f"пропуск {e} {b}: {ss}", file=sys.stderr)
            else:
                by.setdefault(b, {})[e] = ss[0]
    rows, assets, pid = [], [], 0
    for b in sorted(by):
        have = [e for e in exs if e in by[b]]
        if len(have) < a.min_ex:
            continue
        assets.append(b)
        for ea, eb in itertools.combinations(have, 2):
            pid += 1
            leg = lambda e: (e, by[b][e], meta["gate"][by[b][e]] if e == "gate" else "", "", "")
            ct = lambda e: meta["okx"][by[b][e]] if e == "okx" else 1
            (xa, sa, ma, la, ta), (xb, sb, mb, lb, tb) = leg(ea), leg(eb)
            rows.append([len(assets), pid, xa, sa, ma, la, ta, xb, sb, mb, lb, tb, ct(ea), ct(eb)])
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow("asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB".split(","))
        w.writerows(rows)
    with open(a.out.replace(".csv", "") + ".assets.csv", "w") as f:
        f.write("asset,base\n" + "".join(f"{i},{b}\n" for i, b in enumerate(assets, 1)))
    print("активов", len(assets), "пар", len(rows))


main()
