#!/usr/bin/env python3
"""TK-493 F5: собирает словарь псевдонимов asset_alias.csv (П-37 §4.2 [Н29]) из ручного списка + доказательства из asset_scan.csv.
Словарь только ПРЕДЛАГАЕТ кандидатов; решение «один актив» принимает измеренное правило (asset_table.py). Колонки:
exchange, base, key, kind (rename|hk_leg|extra), source, evidence."""
import csv, os
import asset_lib as L

# (exchange, base, key, kind, source)
ENTRIES = [
    # HK-числовые тикеры Binance: нога в HKD (цена в гонконгских долларах, отношение к USDT-ноге ~7,8); коды HKEX - общеизвестные факты
    # для 0700/0992/1810 (подтверждены ещё и измерением), 0625 -> SHEIN только по измерению скана (подтвердить)
    ("binance", "HK0700", "TENCENT", "hk_leg", "словарь: HKEX 0700; ноги в HKD"),
    ("binance", "HK0992", "LENOVO", "hk_leg", "словарь: HKEX 992 (ticker=992HK у Bybit); ноги в HKD"),
    ("binance", "HK1810", "XIAOMI", "hk_leg", "словарь: HKEX 1810; ноги в HKD"),
    ("binance", "HK0625", "SHEIN", "hk_leg", "скан F5 (по данным, код HKEX не подтверждён) - подтвердить"),
    ("bitget", "CSOPSK2LHKD", "CSOPSKHYNIX2L", "hk_leg", "скан F5: нога в HKD, имя другое"),
    ("bitget", "CSOPSS2LHKD", "CSOPSAMSUNG2L", "hk_leg", "скан F5: нога в HKD, имя другое"),
    # переименования тикеров между биржами (скан F5 по «сиротам», ручной просмотр)
    ("bybit", "PUMPFUN", "PUMP", "rename", "скан F5"),
    ("binance", "RAYSOL", "RAY", "rename", "скан F5"),
    ("bybit", "RAYDIUM", "RAY", "rename", "скан F5"),
    ("bingx", "LIGHTER", "LIT", "rename", "скан F5"),
    ("okx", "QUANT", "QNT", "rename", "скан F5"),
    ("bingx", "BNLIFE", "币安人生", "rename", "скан F5"),
    ("bingx", "METEORA", "MET", "rename", "скан F5"),
    ("bingx", "AEONBSC", "AEON", "rename", "скан F5"),
    ("binance", "BEAMX", "BEAM", "rename", "скан F5"),
    ("bingx", "OPENEDEN", "EDEN", "rename", "скан F5"),
    ("bingx", "NOMINA", "NOM", "rename", "скан F5"),
    ("bingx", "FIGHTID", "FIGHT", "rename", "скан F5"),
    ("bybit", "SKYAI1", "SKYAI", "rename", "скан F5"),
    ("bingx", "EDGEX", "EDGE", "rename", "скан F5"),
    ("bingx", "CITREA", "CTR", "rename", "скан F5"),
    ("bingx", "HYPERLANE", "HYPER", "rename", "скан F5"),
    ("bingx", "MONAD", "MON", "rename", "скан F5"),
    ("binance", "DATAIP", "DATA", "rename", "скан F5"),
    ("bingx", "DATANETWORK", "DATA", "rename", "скан F5"),
    ("bingx", "WOTAMALAILE", "我踏马来了", "rename", "скан F5"),
    ("bingx", "SPACECOIN", "SPACE", "rename", "скан F5"),
    ("bybit", "NESA", "NES", "rename", "скан F5 (пограничное по S)"),
    ("bingx", "TRUMPSOL", "TRUMP", "rename", "скан F5"),
    ("bingx", "REDSTONE", "RED", "rename", "скан F5"),
    ("bybit", "SOLAYER", "LAYER", "rename", "скан F5"),
    ("bingx", "BITLIGHT", "LIGHT", "rename", "скан F5"),
    ("bingx", "MIRANETWORK", "MIRA", "rename", "скан F5"),
    ("aster", "FUN", "SPORTFUN", "rename", "скан F5 (пограничное по S; Binance FUN - другой актив, у Aster FUN = SPORTFUN)"),
    ("bitget", "UNITAS", "UP", "rename", "скан F5"),
    ("bingx", "RON", "RONIN", "rename", "скан F5 (пограничное по S)"),
    ("bingx", "GRAMTON", "GRAM", "rename", "скан F5"),
    ("bingx", "OPENLEDGER", "OPEN", "rename", "скан F5"),
    ("bingx", "GENSYN", "GENSYN", "rename", "скан F5"),
    ("binance", "AIGENSYN", "GENSYN", "rename", "скан F5"),
    ("bybit", "AIGENSYN", "GENSYN", "rename", "скан F5"),
    ("okx", "AI", "GENSYN", "extra", "скан F5: OKX AI = токен Gensyn (контр. к Aster/Lighter AI - другой актив; ключ AI оставлен)"),
    ("aster", "MEME", "AMEMECOIN", "rename", "скан F5: Aster MEME = AMEMECOIN BingX (пограничное по S); не MEME других бирж"),
    # акции: тикер у биржи другой
    ("bybit", "NOKIA", "NOK", "rename", "скан F5"),
    ("binance", "GIGADEV", "GIGADEVICE", "rename", "скан F5"),
    ("bingx", "DISNEY", "DIS", "rename", "скан F5"),
    ("bingx", "RAMUS", "RAM", "rename", "скан F5"),
    ("binance", "BBX", "BB", "rename", "скан F5: BlackBerry (BB у Bitget/OKX/BingX), не крипто-BB"),
    ("bybit", "BBX", "BB", "rename", "скан F5: BlackBerry"),
    ("aster", "BBX", "BB", "rename", "скан F5: BlackBerry"),
]


def main():
    scan = {}
    p = os.path.join(L.DATA, "asset_scan.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            k = (r["ex1"], r["sym1"])
            if k not in scan or float(r["corr"]) > float(scan[k]["corr"]):
                scan[k] = r
    inst = {}
    for ex in L.EXCH:
        for r in csv.DictReader(open(os.path.join(L.DATA, "inst", ex + ".csv"), encoding="utf-8")):
            inst.setdefault((ex, r["base"]), []).append(r["symbol"])
    out = os.path.join(L.HERE, "asset_alias.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exchange", "base", "key", "kind", "source", "evidence"])
        for ex, base, key, kind, src in ENTRIES:
            if (ex, base) not in inst:
                raise SystemExit("нет базы в instruments: %s %s" % (ex, base))
            ev = ""
            for sym in inst[(ex, base)]:
                s = scan.get((ex, sym))
                if s:
                    ev = "скан: лучшая пара %s:%s corr=%s S=%s ratio=%s n_ret=%s" % (s["ex2"], s["sym2"], s["corr"], s["S"], s["ratio"], s["n_ret"])
                    break
            w.writerow([ex, base, key, kind, src, ev])
    print(len(ENTRIES), "записей ->", out)


if __name__ == "__main__":
    main()
