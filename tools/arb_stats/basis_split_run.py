"""Счёт basis_split.pair_features по всем парам бирж всех общих монет.

Запуск: python3 basis_split_run.py <каталог книг (parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Переменные: DEAD — json пропусков сбора (gaps_scan.py); COINS=BTC,ETH — только эти монеты (проба).
Пишет pickle: {"t0", "t1", "days", "dead", "pairs": {(монета, A, B): признаки пары}, "mismatch": [...], "info": {...}}.
Пары, где отношение медианных цен вне (0,9; 1,1) (разные активы под одним тикером / ошибка множителя), в счёт не идут — в "mismatch".
"""
import json
import os
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import basis_split  # noqa: E402
import maker_run  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

G = {}


def work_coin(coin):
    g = G
    books, info = {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        keep = (a["e"] >= g["t0"] - 3_600_000) & (a["e"] < g["t1"])
        a = {k: v[keep] for k, v in a.items()}
        if len(a["e"]) < 20:
            continue
        books[ex] = a
        info[ex] = {"sym": sym, "mid": float(np.median((a["bid"] + a["ask"]) / 2)), "events": int(((a["e"] >= g["t0"]) & (a["e"] < g["t1"])).sum())}
    out, mism = {}, []
    exs = [e for e in EXCHANGES if e in books]
    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            ratio = info[a_]["mid"] / info[b_]["mid"]
            if not 0.9 < ratio < 1.1:
                mism.append({"coin": coin, "A": a_, "B": b_, "ratio": ratio})
                continue
            f = basis_split.pair_features(books[a_], books[b_], g["t0"], g["t1"], basis_split.merge_dead(g["dead"], (a_, b_)))
            if f is not None:
                out[(coin, a_, b_)] = f
    return coin, out, mism, info


def main(bwork, t0, t1, out, par=3):
    t0, t1 = t0 // 60000 * 60000, t1 // 60000 * 60000
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    symmap, day = symmap_for(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    dead = json.load(open(os.environ["DEAD"])) if os.environ.get("DEAD") else None
    if dead:   # «пропуск» у самого конца окна (последняя запись раньше t1 на миллисекунды) — не пропуск: иначе все не вышедшие эпизоды отбрасываются
        dead = {ex: [[a, b] for a, b in iv if a < t1 - 5000] for ex, iv in dead.items()}
    G.update({"bwork": str(Path(bwork, "parsed")) if Path(bwork, "parsed").exists() else str(bwork), "t0": t0, "t1": t1, "coins": coins, "meta": meta, "dead": dead})
    only = os.environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    t = time.time()
    pairs, mism, info = {}, [], {}
    with Pool(par) as p:
        for k, (coin, o, m, i) in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            pairs.update(o)
            mism += m
            info[coin] = i
            if k % 20 == 0:
                print(f"{k + 1}/{len(names)} монет, {len(pairs)} пар, {time.time() - t:.0f} с", flush=True)
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "days": basis_split.day_edges(t0, t1), "dead": dead, "pairs": pairs, "mismatch": mism, "info": info,
                     "refs": basis_split.REFS, "xs": basis_split.XS, "kinds": basis_split.KINDS, "lt_edges": basis_split.LT_EDGES,
                     "lags_s": basis_split.LAGS_S, "sf_q": basis_split.SF_Q}, f, protocol=4)
    print({"coins": len(names), "pairs": len(pairs), "mismatch": len(mism), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 3)
