#!/usr/bin/env python3
"""TK-493 F5: статистики всех пар-кандидатов по имени (внутри группы ключа: между биржами, внутри биржи, между классами) -> data/xdl9/pairstats.csv.
Только формирование 01.10-14.10. Без решений: сырые статистики asset_lib.pair_stats."""
import collections, csv, itertools, os, sys, time
import numpy as np
import asset_lib as L, asset_cands as A

def main():
    rows, C = L.load_contracts(lazy=True)      # строки по одной: память не копируется
    A.build(rows, C)
    g = collections.defaultdict(list)
    for d in rows:
        if d["big"] != L.OUT_OF_TEST and d["n_valid"] > 0:
            g[d["key"]].append(d)
            for k2 in d["keys_extra"]:
                g[k2].append(d)
    out = os.path.join(L.DATA, "pairstats.csv")
    t0 = time.time()
    n = 0
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "i1", "i2", "ex1", "sym1", "ex2", "sym2", "big1", "big2", "n_common", "n_ret", "corr", "med_ratio", "k", "dev",
                    "S", "mean_lr", "sd_lr", "sd_l1"])
        rets = {}
        seen = set()
        for key, v in g.items():
            for a, b in itertools.combinations(v, 2):
                if (a["i"], b["i"]) in seen or (b["i"], a["i"]) in seen:
                    continue
                seen.add((a["i"], b["i"]))
                if a["turnover"] < b["turnover"] or (a["turnover"] == b["turnover"] and a["i"] > b["i"]):
                    a, b = b, a
                s = L.pair_stats(C[a["i"]], C[b["i"]])
                w.writerow([key, a["i"], b["i"], a["exchange"], a["symbol"], b["exchange"], b["symbol"], a["big"], b["big"], s["n_common"],
                            s["n_ret"], "%.6f" % s["corr"], "%.9g" % s["med_ratio"], s["k"], "%.6f" % s["dev"], "%.6f" % s["S"],
                            "%.8f" % s["mean_lr"], "%.8f" % s["sd_lr"], "%.8f" % s["sd_l1"]])
                n += 1
    print(n, "pairs", "%.0f s" % (time.time() - t0))

if __name__ == "__main__":
    main()
