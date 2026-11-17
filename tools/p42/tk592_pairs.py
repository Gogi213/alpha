"""П-42 §13.18 (TK-592): перебор пар на U0 независимых подходах — поиск на одной половине, подтверждение на другой (A→B и B→A), ядро.

Использование: python3 tk592_pairs.py <ep_u0.parquet> <каталог выхода>
Выход: cells_search.csv (все читаемые клетки), cells_confirm.csv (шорт-листы с подтверждением), core.json, summary.json, frozen_<side>_<dir>.pkl
(границы корзин и аддитивная модель половины поиска — для отложенных суток).
"""
from __future__ import annotations

import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk592_lib import FEATS, SEED, Additive, confirm_cells, make_buckets, prepare, screen, search_cells, shortlist  # noqa: E402


def main():
    table, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    df = prepare(pd.read_parquet(table))
    search_rows, conf_rows, summ = [], [], {}
    base = []
    for si, side in enumerate(("long", "short")):
        D = df[df.side == side]
        halves = {"A": D[D.half == "A"].reset_index(drop=True), "B": D[D.half == "B"].reset_index(drop=True)}
        for h, H in halves.items():
            base.append({"side": side, "half": h, "n": len(H), "k": int(H.y.sum()), "y_share": float(H.y.mean()), "c_share": float(H.c_f.mean()),
                         "E1": float(H.d.mean()), "u1_n": int(H.in_pool.sum()), "u1_k": int(H[H.in_pool == 1].y.sum())})
        for di, (sh, th) in enumerate((("A", "B"), ("B", "A"))):
            S, T = halves[sh], halves[th]
            add = Additive(S)
            bk = make_buckets(S)
            cells = screen(search_cells(S, side, add, bk))
            sl = shortlist(cells)
            rng = np.random.default_rng([SEED, si, di])
            conf = confirm_cells(T, add, bk, sl, rng)
            tag = f"{side}_{sh}to{th}"
            for c in cells:
                search_rows.append({**c, "direction": f"{sh}->{th}", "in_shortlist": c["id"] in {x["id"] for x in sl}})
            byid = {c["id"]: c for c in cells}
            for x in conf:
                c = byid[x["id"]]
                conf_rows.append({"direction": f"{sh}->{th}", **{k: c[k] for k in ("id", "side", "f1", "l1", "f2", "l2", "scheme", "n", "k", "y_share", "c_share", "E1", "M", "q")},
                                  **{"cf_" + k: v for k, v in x.items() if k not in ("id", "side")}})
            summ[tag] = {"search_n": len(S), "test_n": len(T), "cells_readable": len(cells), "pairs_median": int(sum(c["scheme"] == "median" for c in cells)),
                         "bh_pass_raw": int(sum(c["q"] < 0.10 for c in cells)), "screen_passed": int(sum(c["passed"] for c in cells)),
                         "shortlist": len(sl), "confirmed": int(sum(x["confirmed"] for x in conf)),
                         "tertile_cells": int(sum(c["scheme"] == "tertile" for c in cells))}
            with open(f"{out}/frozen_{tag}.pkl", "wb") as fh:
                pickle.dump({"add": add, "bk": bk, "shortlist": sl, "side": side, "search": sh}, fh)
            print(tag, json.dumps(summ[tag]), flush=True)
    sc = pd.DataFrame(search_rows)
    cf = pd.DataFrame(conf_rows)
    sc.to_csv(f"{out}/cells_search.csv", index=False)
    cf.to_csv(f"{out}/cells_confirm.csv", index=False)
    pd.DataFrame(base).to_csv(f"{out}/base.csv", index=False)
    # подтверждено в обе стороны; ядро
    core_feats, both = set(), []
    if len(cf):
        conf_ids = cf[cf.cf_confirmed]
        for cid, g in conf_ids.groupby("id"):
            if g.direction.nunique() == 2:
                both.append(cid)
        for _, r in conf_ids.iterrows():
            core_feats.update([r.f1, r.f2])
    core = [f for f in FEATS if f in core_feats]
    core_used = core if len(core) >= 3 else list(FEATS)
    # воспроизведено в поиске в обе стороны (описательно): клетка прошла отсев в A-поиске и в B-поиске, знак M один
    rep = []
    if len(sc):
        ps = sc[sc.passed]
        for cid, g in ps.groupby("id"):
            if g.direction.nunique() == 2 and np.sign(g.M).nunique() == 1:
                rep.append(cid)
    json.dump({"core_features_from_confirmed": core, "core_used": core_used, "core_empty_fallback_all12": len(core) < 3,
               "confirmed_both_directions": both, "reproduced_in_search_both_ways": rep}, open(f"{out}/core.json", "w"), ensure_ascii=False, indent=1)
    json.dump(summ, open(f"{out}/summary.json", "w"), ensure_ascii=False, indent=1)
    print("core", core_used, "both", both, "rep", len(rep), flush=True)


if __name__ == "__main__":
    main()
