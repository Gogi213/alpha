"""Этап B: по данным этапа A считает статистику арбитража по всем парам бирж для каждой общей монеты.

Запуск: python3 analyze.py <work> <день> <t0_ms> <t1_ms> <out_dir> [e|rx]
  e  — время события биржи (E): состояние рынка в одну и ту же миллисекунду (основной вид);
  rx — время приёма на srv (поздние записи > 2 с и устаревшие по E отброшены): то, что увидел бы бот на этой машине.
Пишет <out>/result-<clock>.json (строки пар, сводки) и <out>/detail-<clock>.pkl (бары, эпизоды, гистограммы — для графиков).
"""
import glob
import json
import pickle
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
from common import EXCHANGES, build_symmap, load_pool, norm  # noqa: E402

G = {}


def load(work, ex, sym, mult, unit):
    fs = sorted(glob.glob(str(Path(work, ex, sym + ".*.npz"))))
    if not fs:
        return None
    parts = [dict(np.load(f)) for f in fs]
    a = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    a["dep"] = np.minimum(a["bsz"] * a["bid"], a["asz"] * a["ask"]) * unit
    del a["bsz"], a["asz"]
    a["bid"], a["ask"] = a["bid"] / mult, a["ask"] / mult
    return a


def work_coin(coin):
    g = G
    arrs, info = {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = load(g["work"], ex, sym, mult, unit)
        if a is None:
            continue
        lag = a["lag"].copy()
        if g["clock"] == "rx":
            a = core.rx_view(a)
        a = core.clean(a)
        if len(a["e"]) < 20:
            continue
        inw = a["e"] >= g["t0"]
        arrs[ex] = a
        info[ex] = {"sym": sym, "events": int(inw.sum()), "depth_usd": float(np.median(a["dep"][inw])) if inw.any() else None,
                    "mid": float(np.median((a["bid"] + a["ask"]) / 2)), "mult": mult,
                    "lag": lag[:: max(1, len(lag) // 20000)].tolist()}
    exs = [e for e in EXCHANGES if e in arrs]
    rows, det, mism = [], {}, []
    for ia in range(len(exs)):
        for ib in range(ia + 1, len(exs)):
            ea, eb = exs[ia], exs[ib]
            ratio = info[ea]["mid"] / info[eb]["mid"]
            if not 0.9 < ratio < 1.1:
                mism.append({"coin": coin, "A": ea, "B": eb, "ratio": ratio})
                continue
            r = core.analyze_pair(arrs[ea], arrs[eb], g["t0"], g["t1"])
            if r is None:
                continue
            row, bars, eps, hist = r
            row.update({"coin": coin, "A": ea, "B": eb})
            rows.append(row)
            det[(coin, ea, eb)] = {"bars": bars, "eps": eps, "hist": hist}
    return coin, rows, det, mism, {ex: {k: v for k, v in i.items()} for ex, i in info.items()}


def main(work, day, t0, t1, out, clock):
    symmap = build_symmap(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin   # две записи одной монеты на бирже (MASK / NULLMASK): берётся та, чьё имя совпадает
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    G.update({"work": work, "t0": t0, "t1": t1, "clock": clock, "coins": coins, "meta": meta})
    with Pool(int(__import__("os").environ.get("PAR", "3"))) as p:
        res = p.map(work_coin, sorted(coins), chunksize=4)
    rows, det, mism, info = [], {}, [], {}
    for coin, r, d, m, i in res:
        rows += r
        det.update(d)
        mism += m
        info[coin] = i
    lag = {}
    for ex in EXCHANGES:
        v = np.concatenate([np.array(i[ex]["lag"]) for i in info.values() if ex in i and i[ex]["lag"]] or [np.array([0])])
        lag[ex] = {"p50": float(np.percentile(v, 50)), "p99": float(np.percentile(v, 99)), "late": float((v > 2000).mean()), "n": int(len(v))}
    for i in info.values():
        for x in i.values():
            x.pop("lag")
    outd = Path(out)
    outd.mkdir(parents=True, exist_ok=True)
    result = {"clock": clock, "t0": t0, "t1": t1, "levels": core.LEVELS, "edge_x": core.EDGE_X, "tau_edges_ms": core.TAU_EDGES_MS,
              "rows": rows, "mismatch": mism, "info": info, "lag": lag, "coins_total": len(coins)}
    (outd / f"result-{clock}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    with open(outd / f"detail-{clock}.pkl", "wb") as f:
        pickle.dump(det, f, protocol=4)
    print(json.dumps({"rows": len(rows), "coins": len(coins), "mismatch": len(mism)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6] if len(sys.argv) > 6 else "e")
