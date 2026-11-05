#!/usr/bin/env python3
"""TK-493 F5, калибровка порогов правила «один актив» (П-37 §4.2, [Н5, Н29, Н30]) - ТОЛЬКО по формированию 01.10-14.10 UTC.

Основной ярус (определяет пороги): «одно» - BTC и ETH на всех 8 минутных биржах (по 28 пар); «разное» - DASH-акция против DASH-крипты,
Aster MEME против MEME других бирж, OKX AI против AI Aster/Lighter, ETHBTC против ETH, XAU против XAUT/PAXG (все биржевые комбинации).
Расширенный ярус (гипотезы из материала владельца) пороги НЕ двигает: считается отдельно в asset_table.py после фиксации порогов.

Статистики пары - asset_lib.pair_stats (c1 = нога с большим оборотом на формировании, [Н20]); для каждой пары: corr, dev, S, n_ret.
Правило порога (r2, [Н5]): середина зазора между «худшим одно» и «лучшим разное»; нет зазора - калибровка по этой статистике не удалась.
Вывод: calib_pairs.csv (по каждой пере), calib_summary.json (диапазоны, зазоры, пороги).
"""
import csv, itertools, json, os, sys
import numpy as np
import asset_lib as L

BTC = {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "aster": "BTCUSDT",
       "kraken": "PF_XBTUSD", "lighter": "BTC", "bitget": "BTCUSDT"}
ETH = {"binance": "ETHUSDT", "bybit": "ETHUSDT", "okx": "ETH-USDT-SWAP", "bingx": "ETH-USDT", "aster": "ETHUSDT",
       "kraken": "PF_ETHUSD", "lighter": "ETH", "bitget": "ETHUSDT"}
DASH_CRYPTO = {"aster": "DASHUSDT", "binance": "DASHUSDT", "bingx": "DASH-USDT", "bitget": "DASHUSDT", "bybit": "DASHUSDT",
               "kraken": "PF_DASHUSD", "lighter": "DASH", "okx": "DASH-USDT-SWAP"}
DASH_STOCK = [("bingx", "NCSKDASH2USD-USDT"), ("bitget", "DASHSTOCKUSDT")]
MEME_OTHER = {"binance": "MEMEUSDT", "bybit": "MEMEUSDT", "okx": "MEME-USDT-SWAP", "bingx": "MEME-USDT", "bitget": "MEMEUSDT"}
XAU = {"binance": "XAUUSDT", "bybit": "XAUUSDT", "okx": "XAU-USDT-SWAP", "bingx": "NCCOGOLD2USD-USDT", "aster": "XAUUSDT",
       "kraken": "PF_XAUUSD", "lighter": "XAU", "bitget": "XAUUSDT"}
XAUT = {"binance": "XAUTUSDT", "bybit": "XAUTUSDT", "bitget": "XAUTUSDT", "kraken": "PF_XAUTUSD", "bingx": "XAUT-USDT"}
PAXG = {"binance": "PAXGUSDT", "bybit": "PAXGUSDT", "bitget": "PAXGUSDT", "kraken": "PF_PAXGUSD", "lighter": "PAXG",
        "aster": "PAXGUSDT", "bingx": "PAXG-USDT"}
ETHBTC = [("binance", "ETHBTC"), ("bybit", "ETHBTCUSDT")]


def calib_list():
    """-> list[(group, label, kind, legA, legB)]; kind 'одно'/'разное'."""
    out = []
    for nm, m in (("BTC", BTC), ("ETH", ETH)):
        for e1, e2 in itertools.combinations(sorted(m), 2):
            out.append(("%s между биржами" % nm, nm, "одно", (e1, m[e1]), (e2, m[e2])))
    for s in DASH_STOCK:
        for e, sym in DASH_CRYPTO.items():
            out.append(("DASH-акция против DASH-крипты", "DASH", "разное", s, (e, sym)))
    for e, sym in MEME_OTHER.items():
        out.append(("Aster MEME против MEME других", "MEME", "разное", ("aster", "MEMEUSDT"), (e, sym)))
    for e, sym in (("aster", "AIUSDT"), ("lighter", "AI")):
        out.append(("OKX AI против AI Aster/Lighter", "AI", "разное", ("okx", "AI-USDT-SWAP"), (e, sym)))
    for b in ETHBTC:
        for e, sym in ETH.items():
            out.append(("ETHBTC против ETH", "ETHBTC", "разное", b, (e, sym)))
    for e1, s1 in XAU.items():
        for nm, m in (("XAUT", XAUT), ("PAXG", PAXG)):
            for e2, s2 in m.items():
                out.append(("XAU против XAUT/PAXG", "XAU-" + nm, "разное", (e1, s1), (e2, s2)))
    return out


def run(rows, C, N_RET_MIN):
    by = {(d["exchange"], d["symbol"]): d for d in rows}
    res = []
    skipped = []
    for grp, lab, kind, a, b in calib_list():
        da, db = by[a], by[b]
        if da["n_valid"] == 0 or db["n_valid"] == 0:
            skipped.append((grp, a, b, "нет валидных минут у %s:%s" % (a if da["n_valid"] == 0 else b)))
            continue
        if da["turnover"] < db["turnover"]:
            da, db = db, da
        s = L.pair_stats(C[da["i"]], C[db["i"]])
        if s["n_ret"] < N_RET_MIN:
            skipped.append((grp, (da["exchange"], da["symbol"]), (db["exchange"], db["symbol"]), "n_ret=%d < %d" % (s["n_ret"], N_RET_MIN)))
            continue
        res.append(dict(group=grp, label=lab, kind=kind, ex1=da["exchange"], sym1=da["symbol"], ex2=db["exchange"], sym2=db["symbol"], **s))
    return res, skipped


def summarize(res):
    same = [r for r in res if r["kind"] == "одно"]
    diff = [r for r in res if r["kind"] == "разное"]
    out = {"n_same": len(same), "n_diff": len(diff)}
    # corr: выше = «одно»; dev и S: ниже = «одно»
    cmin_same = min(r["corr"] for r in same)
    cmax_diff = max(r["corr"] for r in diff)
    dmax_same = max(r["dev"] for r in same)
    dmin_diff = min(r["dev"] for r in diff)
    smax_same = max(r["S"] for r in same)
    smin_diff = min(r["S"] for r in diff)
    out["corr"] = dict(worst_same=cmin_same, best_diff=cmax_diff, gap=cmin_same > cmax_diff,
                       mid=(cmin_same + cmax_diff) / 2, argmax_diff=max(diff, key=lambda r: r["corr"])["group"])
    out["dev"] = dict(worst_same=dmax_same, best_diff=dmin_diff, gap=dmax_same < dmin_diff, mid=(dmax_same + dmin_diff) / 2,
                      argmin_diff=min(diff, key=lambda r: r["dev"])["group"])
    out["S"] = dict(worst_same=smax_same, best_diff=smin_diff, gap=smax_same < smin_diff, mid=(smax_same + smin_diff) / 2,
                    argmin_diff=min(diff, key=lambda r: r["S"])["group"])
    # по группам «разное»
    gs = {}
    for r in diff:
        g = gs.setdefault(r["group"], dict(n=0, corr_max=-9, corr_min=9, dev_min=9, S_min=9))
        g["n"] += 1
        g["corr_max"] = max(g["corr_max"], r["corr"])
        g["corr_min"] = min(g["corr_min"], r["corr"])
        g["dev_min"] = min(g["dev_min"], r["dev"])
        g["S_min"] = min(g["S_min"], r["S"])
    out["by_group_diff"] = gs
    return out


def main():
    N_RET_MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rows, C = L.load_contracts()
    res, skipped = run(rows, C, N_RET_MIN)
    outdir = L.DATA
    with open(os.path.join(outdir, "calib_pairs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["group", "kind", "ex1", "sym1", "ex2", "sym2", "corr", "med_ratio", "k", "dev", "S", "n_common", "n_ret"])
        for r in res:
            w.writerow([r["group"], r["kind"], r["ex1"], r["sym1"], r["ex2"], r["sym2"], "%.6f" % r["corr"], "%.8g" % r["med_ratio"],
                        r["k"], "%.6f" % r["dev"], "%.6f" % r["S"], r["n_common"], r["n_ret"]])
    summ = summarize(res)
    summ["skipped"] = [dict(group=g, a=list(a), b=list(b), why=w) for g, a, b, w in skipped]
    summ["n_ret_min"] = N_RET_MIN
    with open(os.path.join(outdir, "calib_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summ, fh, ensure_ascii=False, indent=1, default=float)
    print(json.dumps({k: v for k, v in summ.items() if k != "by_group_diff"}, ensure_ascii=False, indent=1, default=float))
    for g, v in summ["by_group_diff"].items():
        print(g, v)


if __name__ == "__main__":
    main()
