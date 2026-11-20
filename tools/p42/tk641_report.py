#!/usr/bin/env python3
"""П-42 §13.38 (TK-641): описание `L_mk2` внутри выборки, пересечение с `L_base` / `L_n24` / `S_base`, шумовой эталон по 8 клеткам G_mk.
  python3 tk641_report.py [--out <файл.json>]     (srv; только чтение готовых CSV G-626c / G-638a, движок не запускается)
Склейка — зачётные сутки окон wa / wb / wc (01.07–13.11, 136 сут.); P&L сделки = gross − fees ($, ставка 500 $, плечо 2), без фандинга.
Остановка (п. 7): sha256 `results.json` G-626c ≠ записанному или Σ / n `L_mk2` ≠ `tk626-frozen.json`; маркеры `READ-*` вперёд."""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
import selection_bias as sb  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402

GMK = "/data/p42-tk626/gmk"
GMK_RESULTS_SHA = "333b1f708581d64d62193439cf1d5bc8c9522d8038d3af54bb2635d7552ef45e"
N24 = "/data/p42-tk638/gate-638a"
FREEZE626 = os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk626-frozen.json")
READ_DIRS = ("/data/p42-tk616/fwd", "/data/p42-tk626")
CELLS = {   # имя → (сторона движка, путь к trades.csv окна)
    "L_mk2": ("long", lambda w: "%s/out/%s/cf280a1e8fc7c/long-trades.csv" % (GMK, w)),
    "L_base": ("long", lambda w: "%s/out/%s/c3dacae86b422/long-trades.csv" % (GMK, w)),
    "L_n24": ("long", lambda w: "%s/%s-L/N24_q60/long-trades.csv" % (N24, w)),
    "S_base": ("short", lambda w: "%s/out/%s/c8c06a7d70b92/short-trades.csv" % (GMK, w)),
}
N_TRIED = 8          # п. 6: G_mk на 566 (4 клетки) + на 775 (4 клетки)
NBOOT = 100_000
SEED = int(hashlib.md5(b"TK-641").hexdigest()[:8], 16)
MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(cell):
    """Сделки клетки в зачётных сутках: словари с ключом сигнала, сутками, P&L, временами (мс эпохи)."""
    _, path = CELLS[cell]
    rows = []
    for w in st.TRADE_WINDOWS:
        t0 = w.t0_ms
        with open(path(w.name), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                d = int(st.abs_day(w, np.array([int(r["entry_ms"])]))[0])
                if (w.lod - st.D0).days <= d <= (w.hid - st.D0).days:
                    rows.append(dict(sym=r["sym"], level=r["level"], sig=t0 + int(r["sig_ms"]), t_in=t0 + int(r["entry_ms"]), t_out=t0 + int(r["exit_ms"]),
                                     day=d, pnl=float(r["gross"]) - float(r["fees"]), kind=r["kind"]))
    return rows


def daily(rows):
    a = np.zeros(st.NDAYS)
    for r in rows:
        a[r["day"]] += r["pnl"]
    return a


def boot_mean(rows, nboot=NBOOT, block=st.BLOCK, seed=SEED):
    """90 % интервал ΣS/ΣN круговым блочным бутстрепом по суткам (блок 4)."""
    S, N = np.zeros(st.NDAYS), np.zeros(st.NDAYS)
    for r in rows:
        S[r["day"]] += r["pnl"]
        N[r["day"]] += 1
    rng = np.random.default_rng(seed)
    nb = math.ceil(st.NDAYS / block)
    starts = rng.integers(0, st.NDAYS, size=(nboot, nb))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % st.NDAYS).reshape(nboot, -1)[:, :st.NDAYS]
    m = S[idx].sum(1) / np.maximum(N[idx].sum(1), 1)
    return float(np.quantile(m, 0.05)), float(np.quantile(m, 0.95))


def maxdd(rows):
    eq = np.cumsum([r["pnl"] for r in sorted(rows, key=lambda r: r["t_out"])])
    return float((np.maximum.accumulate(np.concatenate([[0.0], eq])) - np.concatenate([[0.0], eq])).max()) if len(eq) else 0.0


def sharpe(dl):
    s = dl.std(ddof=1)
    return float(dl.mean() / s * math.sqrt(365)) if s > 0 else float("nan")


def describe(rows):
    dl = daily(rows)
    n, S = len(rows), float(sum(r["pnl"] for r in rows))
    mon = []
    for k, _ in enumerate(MONTHS):
        rk = [r for r in rows if st.MONTH_OF_DAY[r["day"]] == k]
        sk = sum(r["pnl"] for r in rk)
        mon.append(dict(month=MONTHS[k], n=len(rk), sum=sk, mean=sk / len(rk) if rk else float("nan")))
    lo, hi = boot_mean(rows)
    r = rr.rr_stats(rows)
    return dict(n=n, sum=S, mean=S / n if n else float("nan"), per_day=S / st.NDAYS, sharpe=sharpe(dl), lo90=lo, hi90=hi, months=mon,
                rr=r["rr"], stops=r["stops"], maxdd=maxdd(rows))


def overlap(a, b, same_side):
    ka = {(r["sym"], r["level"], r["sig"]): r for r in a}
    kb = {(r["sym"], r["level"], r["sig"]): r for r in b}
    both = ka.keys() & kb.keys()
    cd = lambda x: {(r["sym"], r["day"]) for r in x}  # noqa: E731
    ca, cb = cd(a), cd(b)
    out = dict(corr_daily=float(np.corrcoef(daily(a), daily(b))[0, 1]), jaccard_coinday=len(ca & cb) / len(ca | cb) if ca | cb else float("nan"))
    if same_side:
        out.update(common=len(both), share_of_a=len(both) / len(ka), share_of_b=len(both) / len(kb), jaccard_sig=len(both) / len(ka.keys() | kb.keys()),
                   sum_common_a=sum(ka[k]["pnl"] for k in both), sum_common_b=sum(kb[k]["pnl"] for k in both),
                   sum_only_a=sum(ka[k]["pnl"] for k in ka.keys() - both), sum_only_b=sum(kb[k]["pnl"] for k in kb.keys() - both),
                   n_only_a=len(ka.keys() - both), n_only_b=len(kb.keys() - both))
    else:
        by = {}
        for r in b:
            by.setdefault(r["sym"], []).append((r["t_in"], r["t_out"]))
        out["hold_overlap_pairs"] = sum(1 for r in a for (i, o) in by.get(r["sym"], ()) if r["t_in"] < o and i < r["t_out"])
    return out


def noise(sh):
    se = math.sqrt(365.0 / st.NDAYS * (1 + sh * sh / 730.0))
    return sb.significance(sh, se, N_TRIED)


def guards():
    miss = [CELLS[c][1](w.name) for c in CELLS for w in st.TRADE_WINDOWS if not os.path.isfile(CELLS[c][1](w.name))]
    if miss:
        raise SystemExit("STOP: нет CSV окна: %s" % miss)
    got = sha256(os.path.join(GMK, "results.json"))
    if got != GMK_RESULTS_SHA:
        raise SystemExit("STOP: results.json G-626c sha256 %s ≠ %s" % (got, GMK_RESULTS_SHA))
    marks = [p for d in READ_DIRS for p in glob.glob(os.path.join(d, "**", "READ-*"), recursive=True)]
    if marks:
        raise SystemExit("STOP: маркеры чтения вперёд: %s" % marks[:5])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    guards()
    R = {c: load(c) for c in CELLS}
    ref = json.load(open(FREEZE626, encoding="utf-8"))["gates"]["G-626c"]["cells"]["L_mk2"]
    S = sum(r["pnl"] for r in R["L_mk2"])
    if len(R["L_mk2"]) != ref["n"] or abs(S - ref["sum"]) > 0.01:
        raise SystemExit("STOP: L_mk2 n %d Σ %.4f ≠ G-626c n %d Σ %.4f" % (len(R["L_mk2"]), S, ref["n"], ref["sum"]))
    if any(r["day"] >= st.NDAYS for c in R for r in R[c]):
        raise SystemExit("STOP: сутки позже 13.11")
    out = dict(desc={c: describe(R[c]) for c in CELLS},
               overlap={o: overlap(R["L_mk2"], R[o], o != "S_base") for o in ("L_base", "L_n24", "S_base")},
               overlap_n24_base=overlap(R["L_n24"], R["L_base"], True))
    out["noise"] = noise(out["desc"]["L_mk2"]["sharpe"])
    out["noise"]["n_tried"] = N_TRIED
    out["seed"] = SEED
    out["nboot"] = NBOOT
    js = json.dumps(out, indent=1, ensure_ascii=False, default=float)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(js)
    print(js)


if __name__ == "__main__":
    main()
