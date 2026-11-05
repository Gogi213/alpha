#!/usr/bin/env python3
"""TK-493 F5: расширенный ярус калибровки (гипотезы H1-H7 из материала владельца, П-37 §4.2-§4.3): статистики и вердикт по порогам,
зафиксированным по основному ярусу. Пороги НЕ двигает. Только формирование 01.10-14.10. Вывод: data/xdl9/calib_extended.csv."""
import csv, json, os
import asset_lib as L

PAIRS = [  # (метка, [нога A], [нога B]) - ноги как (биржа, символ)
    ("H1 ZHIPU Binance x Bitget", [("binance", "ZHIPUUSDT")], [("bitget", "ZHIPUUSDT"), ("bitget", "ZHIPUHKDUSDT")]),
    ("H1 MINIMAX Binance x Bitget", [("binance", "MINIMAXUSDT")], [("bitget", "MINIMAXUSDT"), ("bitget", "MINIMAXHKDUSDT")]),
    ("H1 TENCENT Binance x Bitget", [("binance", "TENCENTUSDT"), ("binance", "HK0700USDT")], [("bitget", "TENCENTUSDT"), ("bitget", "TENCENTHKDUSDT")]),
    ("H1 LENOVO Binance x Bitget", [("binance", "HK0992USDT")], [("bitget", "LENOVOHKDUSDT")]),
    ("H1 LENOVO Bybit x Bitget", [("bybit", "LENOVOUSDT")], [("bitget", "LENOVOHKDUSDT")]),
    ("H1 XIAOMI Binance x Bitget", [("binance", "HK1810USDT")], [("bitget", "XIAOMIUSDT"), ("bitget", "XIAOMIHKDUSDT")]),
    ("H2 BRETT BingX x Bitget", [("bingx", "BRETT-USDT")], [("bitget", "BRETTUSDT")]),
    ("H3 LAB Binance x Aster", [("binance", "LABUSDT")], [("aster", "LABUSDT")]),
    ("H4 TA Binance x Aster", [("binance", "TAUSDT")], [("aster", "TAUSDT")]),
    ("H4 TA Binance x BingX", [("binance", "TAUSDT")], [("bingx", "TA-USDT")]),
    ("H6 LPT Binance x BingX", [("binance", "LPTUSDT")], [("bingx", "LPT-USDT")]),
    ("H7 WCT Binance x BingX", [("binance", "WCTUSDT")], [("bingx", "WCT-USDT")]),
    ("H7 H Binance x BingX", [("binance", "HUSDT")], [("bingx", "H-USDT")]),
]

def main():
    rows, C = L.load_contracts()
    by = {(d["exchange"], d["symbol"]): d for d in rows}
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    mid, hi = cal["S"]["mid"], cal["S"]["best_diff"]
    out = []
    for lab, A, B in PAIRS:
        for a in A:
            for b in B:
                da, db = by.get(a), by.get(b)
                if da is None or db is None or da["n_valid"] == 0 or db["n_valid"] == 0:
                    out.append(dict(label=lab, ex1=a[0], sym1=a[1], ex2=b[0], sym2=b[1], verdict="нет данных (нет контракта или валидных минут)"))
                    continue
                if da["turnover"] < db["turnover"]:
                    da, db = db, da
                s = L.pair_stats(C[da["i"]], C[db["i"]])
                v = ("нет данных (n_ret=%d < 300)" % s["n_ret"]) if s["n_ret"] < 300 else ("одно" if s["S"] <= mid else ("пограничное" if s["S"] < hi else "разное"))
                out.append(dict(label=lab, ex1=da["exchange"], sym1=da["symbol"], ex2=db["exchange"], sym2=db["symbol"], n_ret=s["n_ret"],
                                corr="%.4f" % s["corr"], med_ratio="%.6g" % s["med_ratio"], dev="%.5f" % s["dev"], S="%.4f" % s["S"], verdict=v))
    keys = ["label", "ex1", "sym1", "ex2", "sym2", "n_ret", "corr", "med_ratio", "dev", "S", "verdict"]
    with open(os.path.join(L.DATA, "calib_extended.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(out)
    for o in out:
        print(" | ".join(str(o.get(k, "")) for k in keys))

if __name__ == "__main__":
    main()
