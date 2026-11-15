"""Счёт «сделки BingX как событийная цена» по всем монетам с BingX и хотя бы одной быстрой биржей (Binance/Bybit/OKX), окно t0…t1.

Запуск: python3 bx_trades_run.py <каталог книг (parsed/)> <каталог сделок BingX (bx_trades_parse.py, w/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Переменные: DEAD — json пропусков сбора (gaps_scan.py); COINS=BTC,ETH — только эти монеты (проба).
Пишет pickle: {"t0", "t1", "days", "coins": {монета: {"sym", "stats": freq_stats, "F": {биржа: {"ll", "div", "snap"}}}}, "mismatch"}.
Пары, где отношение медианных цен вне (0,9; 1,1), в счёт не идут (как basis_split_run.py).
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
import bx_trades as bx  # noqa: E402
import maker_run  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

G = {}
FAST = ("binance", "bybit", "okx")


def load_book(ex, sym, mult, t0, t1):
    unit = float((G["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
    a = maker_run.load_book(G["bwork"], ex, sym, mult, unit)
    if a is None or len(a["e"]) < 20:
        return None
    keep = (a["e"] >= t0 - 3_600_000) & (a["e"] < t1)
    a = {k: v[keep] for k, v in a.items()}
    return a if len(a["e"]) >= 20 else None


def work_coin(coin):
    g, t0, t1 = G, G["t0"], G["t1"]
    syms = g["coins"][coin]
    if "bingx" not in syms:
        return coin, None, []
    sym, mult, _ = syms["bingx"]
    tr = bx.load_trades(g["twork"], sym, mult, t0, t1)
    X = load_book("bingx", sym, mult, t0, t1)
    if X is None:
        return coin, None, []
    B = bx.make_batches(tr) if tr is not None else None
    res = {"sym": sym, "stats": bx.freq_stats(B, len(tr["T"]), t0, t1) if B is not None else None, "F": {}, "bx_rows": int(((X["e"] >= t0) & (X["e"] < t1)).sum())}
    mism = []
    xmid = float(np.median((X["bid"] + X["ask"]) / 2))
    for ex in FAST:
        if ex not in syms or B is None:
            continue
        fs, fm, _ = syms[ex]
        F = load_book(ex, fs, fm, t0, t1)
        if F is None:
            continue
        ratio = float(np.median((F["bid"] + F["ask"]) / 2)) / xmid
        if not 0.9 < ratio < 1.1:
            mism.append({"coin": coin, "F": ex, "ratio": ratio})
            continue
        cs = bx.centre_series(F, X, t1)
        if cs is None:
            continue
        T, S, Bu, m0, cen = cs
        dead = bx.merge_dead(g["dead"], (ex, "bingx"))
        Gp = bx.trade_gaps(F, B, m0, cen, X)
        res["F"][ex] = {"ll": bx.lead_lag(F, X, B, Gp, t0, t1, dead=dead),
                        "div": {mode: bx.trade_divergence(F, B, Gp, t0, t1, mode, dead=dead) for mode in ("c", "0")},
                        "snap": {mode: bx.snapshot_divergence(T, S, Bu, m0, cen, B, Gp, t0, t1, mode, dead=dead) for mode in ("c", "0")},
                        "f_rows": int(((F["e"] >= t0) & (F["e"] < t1)).sum())}
    return coin, res, mism


def main(bwork, twork, t0, t1, out, par=3):
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
    if dead:
        dead = {ex: [[a, b] for a, b in iv if a < t1 - 5000] for ex, iv in dead.items()}
    G.update({"bwork": str(Path(bwork, "parsed")) if Path(bwork, "parsed").exists() else str(bwork), "twork": str(twork), "t0": t0, "t1": t1,
              "coins": coins, "meta": meta, "dead": dead})
    only = os.environ.get("COINS")
    names = sorted(c for c in coins if "bingx" in coins[c] and any(e in coins[c] for e in FAST)) if not only else only.split(",")
    t = time.time()
    res, mism = {}, []
    with Pool(par) as p:
        for k, (coin, r, m) in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            if r is not None:
                res[coin] = r
            mism += m
            if k % 20 == 0:
                print(f"{k + 1}/{len(names)} монет, {time.time() - t:.0f} с", flush=True)
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "days": bx.day_edges(t0, t1), "dead": dead, "coins": res, "mismatch": mism,
                     "params": {"XS": bx.XS, "THETAS": bx.THETAS, "HORIZON_MS": bx.HORIZON_MS, "EP_GAP_MS": bx.EP_GAP_MS, "DUR_EDGES": bx.DUR_EDGES,
                                "GAP_LO": bx.GAP_LO, "GAP_N": bx.GAP_N, "TAUS_MS": bx.TAUS_MS}}, f, protocol=4)
    print({"coins": len(res), "mismatch": len(mism), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 3)
