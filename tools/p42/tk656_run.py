#!/usr/bin/env python3
"""П-42 §13.56 (TK-656): режим BTC на сигнале — ход BTC за 1 / 4 / 24 ч до сигнала в сторону сделки — как фильтр в $ на h775. Без P&L:
  python3 tk656_run.py feat     — ход BTC (закрытие минуты t − 1 к закрытию t − 1 − W, бп, × знак стороны) на кандидатах баз h775 → /data/p42-tk656/feat/h775/<окно>-<сторона>.csv
  python3 tk656_run.py gate     — G-eq (W = 5 / 15 = готовым `btc_mv5` / `btc_mv15` f12, все строки эпизодов) и G3x (мусор с минуты сигнала не меняет значение)
  python3 tk656_run.py counts   — общие пороги q20 / q80 (сигналы базы h775 стороны, зачётные диапазоны), доли исключённых, остаток сделок базы по клеткам, таблицы `--ftab`
P&L не читается: из файла сделок — только ключи (окно, монета, мс сигнала) — `tk634_run.trade_rows`."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk636_feat as f36  # noqa: E402
import tk638_run as t8  # noqa: E402

ROOT = "/data/p42-tk656"
FEAT34 = "/data/p42-tk634/feat"
UNI = "h775"
SIDES = st.SIDES
SIGN = {"L": 1.0, "S": -1.0}
MIN_MS = 60_000
WS = (60, 240, 1440)                       # п. 4: окна хода BTC, минуты (1 / 4 / 24 ч)
WS_GEQ = (5, 15)                           # сверка с f12 `btc_mv5` / `btc_mv15`
GEQ_TOL = 1e-9
QLO, QHI = 0.2, 0.8
MIN_TRADES = 70
GuardError = t8.GuardError
say, sub, sig2 = t8.say, t8.sub, t8.sig2
in_scored_range = t8.in_scored_range


def qq(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def ffill_close(o, c):
    """Последнее закрытие с данными (float64), до первой минутки с данными — NaN (как `sigfeat_lib.ffill_close`)."""
    cf = np.where(o != 0, c.astype(np.float64), np.nan)
    idx = np.where(o != 0, np.arange(len(cf)), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, cf[np.maximum(idx, 0)], np.nan)


def btc_close(u, w):
    o, c = f36.load_b42(os.path.join(u.wdir(w.name), "cache", "BTCUSDT.b42"))
    return ffill_close(o, c)


def btc_moves(cf, sig_ms, sign, ws):
    """{W: массив} хода BTC в бп × знак стороны: (cf[t − 1] / cf[t − 1 − W] − 1)·1e4·s, t = sig_ms // 60 000 (как f12 `btc_mv5/15`); вне кэша — NaN."""
    t = np.asarray(sig_ms, dtype=np.int64) // MIN_MS
    out = {}
    for W in ws:
        a, b = t - 1, t - 1 - W
        ok = (b >= 0) & (a < len(cf))
        v = np.full(len(t), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            v[ok] = (cf[a[ok]] / cf[b[ok]] - 1.0) * 1e4 * sign
        out[W] = v
    return out


def feat_path(wname, side):
    return os.path.join(ROOT, "feat", UNI, "%s-%s.csv" % (wname, side))


def read_keys(path):
    syms, ms = [], []
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            s, m = line.split(",", 2)[:2]
            syms.append(s)
            ms.append(int(m))
    return syms, np.array(ms, dtype=np.int64)


def cmd_feat(a):
    u = r5.Uni(UNI)
    info = {}
    for w in st.TRADE_WINDOWS:
        cf = btc_close(u, w)
        for side in SIDES:
            syms, ms = read_keys(os.path.join(FEAT34, UNI, "%s-%s.csv" % (w.name, side)))
            mv = btc_moves(cf, ms, SIGN[side], WS)
            path = qq("feat", UNI, "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join("btc%d" % W for W in WS) + "\n")
                for i in range(len(syms)):
                    f.write("%s,%d,%s\n" % (syms[i], int(ms[i]), ",".join(f36.fmt(mv[W][i]) for W in WS)))
            info["%s-%s" % (w.name, side)] = dict(rows=len(syms), nan={str(W): int(np.isnan(mv[W]).sum()) for W in WS},
                                                  sha256=hashlib.sha256(open(path, "rb").read()).hexdigest())
            say("feat %s-%s: %d строк, NaN %s" % (w.name, side, len(syms), info["%s-%s" % (w.name, side)]["nan"]))
    json.dump(info, open(qq("feat", UNI, "info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_gate(a):
    """G-eq: формула на W = 5 / 15 = `btc_mv5` / `btc_mv15` готовых эпизодов f12 (tk634) на всех строках; G3x: мусор в минутках ≥ t не меняет btc60/240/1440 у k случайных кандидатов."""
    import pandas as pd
    u = r5.Uni(UNI)
    rng = np.random.default_rng(656)
    res, bad = dict(geq={}, g3x={}), []
    for w in st.TRADE_WINDOWS:
        o, c = f36.load_b42(os.path.join(u.wdir(w.name), "cache", "BTCUSDT.b42"))
        cf = ffill_close(o, c)
        for side in SIDES:
            eps = pd.read_parquet(os.path.join(FEAT34, UNI, "%s-%s" % (w.name, side), "eps-%s.parquet" % side), columns=["sym", "sig_ms", "btc_mv5", "btc_mv15"])
            mv = btc_moves(cf, eps["sig_ms"].to_numpy(np.int64), SIGN[side], WS_GEQ)
            for W in WS_GEQ:
                x, y = mv[W], eps["btc_mv%d" % W].to_numpy(np.float64)
                both = np.isfinite(x) & np.isfinite(y)
                r = dict(rows=int(len(x)), both=int(both.sum()), only_mine=int((np.isfinite(x) & ~np.isfinite(y)).sum()), only_old=int((~np.isfinite(x) & np.isfinite(y)).sum()),
                         max_abs=float(np.abs(x[both] - y[both]).max()) if both.any() else 0.0)
                res["geq"]["%s-%s/btc_mv%d" % (w.name, side, W)] = r
                if r["max_abs"] > GEQ_TOL or r["only_mine"] or r["only_old"] or r["both"] == 0:
                    bad.append("G-eq %s-%s W=%d: %s" % (w.name, side, W, r))
            syms, ms = read_keys(feat_path(w.name, side))
            ref = btc_moves(cf, ms, SIGN[side], WS)
            pick = rng.choice(len(ms), size=min(a.k, len(ms)), replace=False)
            nbad = 0
            for i in pick:
                t = int(ms[i]) // MIN_MS
                o2, c2 = o.copy(), c.copy()
                o2[t:] = 7
                c2[t:] = rng.integers(1, 10**12, size=len(c2) - t)
                g = btc_moves(ffill_close(o2, c2), ms[i:i + 1], SIGN[side], WS)
                if any(not (np.isnan(g[W][0]) and np.isnan(ref[W][i])) and g[W][0] != ref[W][i] for W in WS):
                    nbad += 1
            res["g3x"]["%s-%s" % (w.name, side)] = dict(k=int(len(pick)), bad=nbad)
            if nbad:
                bad.append("G3x %s-%s: %d из %d" % (w.name, side, nbad, len(pick)))
    res["bad"] = bad
    json.dump(res, open(qq("gates", "gate56.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gate:", "FAIL " + "; ".join(bad) if bad else "ok: G-eq %d сверок, max|Δ| %.2e; G3x %d кандидатов, 0 расхождений" % (
        len(res["geq"]), max(v["max_abs"] for v in res["geq"].values()), sum(v["k"] for v in res["g3x"].values())))
    if bad:
        sys.exit(1)


def load_window(w, side):
    syms, ms = read_keys(feat_path(w.name, side))
    k34, m34 = read_keys(os.path.join(FEAT34, UNI, "%s-%s.csv" % (w.name, side)))
    if syms != k34 or not np.array_equal(ms, m34):
        raise GuardError("%s-%s: ключи tk656 ≠ таблица признаков tk634" % (w.name, side))
    import pandas as pd
    d = pd.read_csv(feat_path(w.name, side))
    return dict(sym=np.array(syms, dtype=object), ms=ms, **{"btc%d" % W: d["btc%d" % W].to_numpy(np.float64) for W in WS})


def thr_path():
    return qq("res", "thr56.json")


def compute_thresholds(tabs):
    """Общие пороги q20 / q80 хода BTC (бп, со знаком стороны) по сигналам базы стороны в зачётных диапазонах трёх окон (linear), 2 значащие цифры; NaN не входит."""
    thr = {}
    for W in WS:
        col = "btc%d" % W
        v = np.concatenate([tabs[w.name][col][in_scored_range(w, tabs[w.name]["ms"]) & np.isfinite(tabs[w.name][col])] for w in st.TRADE_WINDOWS])
        thr[str(W)] = dict(n=int(len(v)), q20_raw=float(np.quantile(v, QLO, method="linear")), q80_raw=float(np.quantile(v, QHI, method="linear")),
                           q20=sig2(float(np.quantile(v, QLO, method="linear"))), q80=sig2(float(np.quantile(v, QHI, method="linear"))),
                           share_pos=float((v > 0).mean()), share_zero=float((v == 0).mean()))
    return thr


def cell_defs(thr):
    """Клетки п. 5: на каждое окно W — with (исключить ход < 0), agst (исключить > 0), nag20 (исключить < q20), nwi80 (исключить > q80)."""
    cells = []
    for W in WS:
        t = thr[str(W)]
        cells += [dict(cid="B%d_with" % W, W=W, op="lt", X=0.0), dict(cid="B%d_agst" % W, W=W, op="gt", X=0.0),
                  dict(cid="B%d_nag20" % W, W=W, op="lt", X=t["q20"]), dict(cid="B%d_nwi80" % W, W=W, op="gt", X=t["q80"])]
    return cells


def excluded(c, t):
    v = t["btc%d" % c["W"]]
    with np.errstate(invalid="ignore"):
        bad = (v < c["X"]) if c["op"] == "lt" else (v > c["X"])
    return np.isfinite(v) & bad


def label(c):
    h = {60: "1 ч", 240: "4 ч", 1440: "24 ч"}[c["W"]]
    what = {"with": "BTC за %s в сторону сделки (≥ 0)", "agst": "BTC за %s против сделки (≤ 0)",
            "nag20": "без сильного хода BTC против за %s (≥ q20)", "nwi80": "без сильного хода BTC по сделке за %s (≤ q80)"}[c["cid"].split("_")[1]] % h
    return "%s, порог %s %%" % (what, ("%g" % (c["X"] / 100)).replace(".", ","))


def cmd_counts(a):
    u = r5.Uni(UNI)
    allthr = {}
    for side in SIDES:
        tabs = {w.name: load_window(w, side) for w in st.TRADE_WINDOWS}
        thr = compute_thresholds(tabs)
        allthr[side] = thr
        cells = cell_defs(thr)
        W_, S_, R_ = r34.trade_rows(u, side)
        idx = {w.name: {(x, int(r)): i for i, (x, r) in enumerate(zip(tabs[w.name]["sym"], tabs[w.name]["ms"]))} for w in st.TRADE_WINDOWS}
        tr = {"btc%d" % W: np.full(len(W_), np.nan) for W in WS}
        for w in st.TRADE_WINDOWS:
            sel = np.nonzero(W_ == w.name)[0]
            pos = np.array([idx[w.name].get((S_[i], int(R_[i])), -1) for i in sel], dtype=np.int64)
            if (pos < 0).any():
                raise GuardError("%s %s: %d сделок базы без строки таблицы кандидатов" % (side, w.name, int((pos < 0).sum())))
            for k in tr:
                tr[k][sel] = tabs[w.name][k][pos]
        n_t = len(W_)
        res = dict(side=side, n_trades=n_t, thr=thr, nan_trades={k: int(np.isnan(v).sum()) for k, v in tr.items()},
                   dist_trades={k: [float(x) for x in np.nanquantile(v, [0.05, 0.25, 0.5, 0.75, 0.95])] for k, v in tr.items()}, cells=[])
        say("== %s: сделок базы %d; NaN на сделках %s" % (side, n_t, res["nan_trades"]))
        for W in WS:
            t = thr[str(W)]
            say("   W=%d: сигналов %d, q20 %g бп, q80 %g бп, доля > 0 %.3f; сделки — квантили 5/25/50/75/95 %s" % (
                W, t["n"], t["q20"], t["q80"], t["share_pos"], [round(x, 1) for x in res["dist_trades"]["btc%d" % W]]))
        for c in cells:
            ex_c = n_c = 0
            for w in st.TRADE_WINDOWS:
                sc = in_scored_range(w, tabs[w.name]["ms"])
                ex_c += int((excluded(c, tabs[w.name]) & sc).sum())
                n_c += int(sc.sum())
            ex_t = excluded(c, tr)
            res["cells"].append(dict(cid=c["cid"], W=c["W"], op=c["op"], X=c["X"], label=label(c), cand_excluded=ex_c, cand_rows=n_c,
                                     trades_excluded=int(ex_t.sum()), trades_remain=int((~ex_t).sum())))
            say("   %-12s %-62s исключено кандидатов %d из %d (%.1f %%), сделок базы %d из %d, остаётся %d%s" % (
                c["cid"], label(c), ex_c, n_c, 100 * ex_c / n_c, int(ex_t.sum()), n_t, int((~ex_t).sum()), "  [мало сделок]" if int((~ex_t).sum()) < MIN_TRADES else ""))
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            cols = {"ALL1": np.ones(len(t["sym"]))}
            cols.update({c["cid"]: np.where(excluded(c, t), 0.0, 1.0) for c in cells})
            path = qq(sub(u, "ftab56"), "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols) + "\n")
                for i in range(len(t["sym"])):
                    f.write("%s,%d,%s\n" % (t["sym"][i], int(t["ms"][i]), ",".join("%.1f" % cols[k][i] for k in cols)))
        json.dump(res, open(qq("res", "counts56-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(allthr, open(thr_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("counts готово — запись в протокол §13.56 п. 10 до чтения P&L")


COMMANDS = {"feat": cmd_feat, "gate": cmd_gate, "counts": cmd_counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--k", type=int, default=120)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
