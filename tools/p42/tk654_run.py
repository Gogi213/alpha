#!/usr/bin/env python3
"""П-42 §13.54 (TK-654): качество уровня (чистый / зона), резкий подход без проторговки, разреженность и пробиваемость монеты `bk` — фильтры в $ на h775. Счёт без P&L:
  python3 tk654_run.py gu0               — G-u0: строки дампов `b0p0` 566 общих монет h775 (TK-625) = дампам TK-604, 4 окна × 2 стороны
  python3 tk654_run.py ep [--gate]       — эпизоды U0 209 новых монет h775 (`tk654_episodes.py`, 4 окна); `--gate` — 10 случайных общих монет (зерно 654) для G-ep
  python3 tk654_run.py gep               — G-ep: эпизоды обёртки на общих монетах = `/data/p42-tk604/ep` (все столбцы, кроме `in_pool`)
  python3 tk654_run.py counts            — G-key, пороги (общие квантили), клетки и дубли, доли исключённых, остаток сделок базы, таблицы `--ftab`
P&L не печатается и не считается: из файла сделок читаются только ключи (окно, монета, мс сигнала) — `tk634_run.trade_rows`."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import retitr_ftab as rfx  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_run as t8  # noqa: E402

ROOT = "/data/p42-tk654"
FEAT34 = "/data/p42-tk634/feat"
EP604 = "/data/p42-tk604/ep"
ENG604 = "/data/p42-tk604/eng"
SIDES = st.SIDES
QS = (0.2, 0.4, 0.6, 0.8)
MIN_TRADES = 70
GATE_SEED, GATE_COINS = 654, 10
# лестницы п. 5: (семейство, столбец)
LADDERS = (("PE", "pierce_ep"), ("ST", "spread_tau"), ("MV5", "mv5"), ("CN60", "cons60"), ("ZV6", "zv6h"), ("TPM60", "tpm60"))
EPS_EXTRA = ("pierce_ep", "spread_tau", "cons60", "zv6h", "mv5")     # из эпизодов f12 (parquet TK-634); mv5 — для G-key
GuardError = t8.GuardError
say, sig2, in_scored_range = t8.say, t8.sig2, t8.in_scored_range
U = r5.Uni("h775")


def qq(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def o566_coins():
    return sorted(x for x in open(r4.SYMS, encoding="utf-8").read().replace("\n", ",").split(",") if x)


def new_coins():
    old = set(o566_coins())
    return [c for c in U.coins() if c not in old]


# ---- G-u0 ---------------------------------------------------------------------------------------------------------------------

def dump_lines(path, keep):
    with open(path, encoding="utf-8", newline="") as f:
        head = f.readline()
        return head, sorted(ln for ln in f if ln.split(",", 1)[0] in keep)


def cmd_gu0(a):
    common = set(o566_coins()) & set(U.coins())
    if len(common) != 566:
        raise GuardError("общих монет %d ≠ 566" % len(common))
    res, bad = {}, []
    for wn in st.ALLOWED_WINDOWS:
        for side in ("long", "short"):
            h1, a1 = dump_lines(os.path.join(U.eng(wn), "b0p0", side + "-sigsx.csv"), common)
            h2, a2 = dump_lines(os.path.join(ENG604, wn, "b0p0", side + "-sigsx.csv"), common)
            eq = h1 == h2 and a1 == a2
            res["%s/%s" % (wn, side)] = dict(rows_h775=len(a1), rows_tk604=len(a2), equal=eq)
            if not eq:
                bad.append("%s/%s: строк %d / %d, заголовок %s" % (wn, side, len(a1), len(a2), "равен" if h1 == h2 else "различен"))
    r4.write_json(qq("gates", "gu0.json"), dict(res=res, bad=bad, common=len(common)))
    say("G-u0", "FAIL " + "; ".join(bad) if bad else "PASS: строки b0p0 566 общих монет равны во всех 8 дампах " + json.dumps(res, ensure_ascii=False))
    if bad:
        sys.exit(1)


# ---- эпизоды ------------------------------------------------------------------------------------------------------------------

def gate_coins():
    common = sorted(set(o566_coins()) & set(U.coins()))
    return sorted(random.Random(GATE_SEED).sample(common, GATE_COINS))


def ep_path(kind, wn):
    return qq("ep", "%s-%s.csv" % (kind, wn))


def cmd_ep(a):
    kind = "gate" if a.gate else "new"
    syms = gate_coins() if a.gate else new_coins()
    sf = qq("ep", "syms-%s.txt" % kind)
    open(sf, "w", encoding="utf-8").write(",".join(syms) + "\n")
    say("эпизоды %s: монет %d" % (kind, len(syms)))
    for wn in st.ALLOWED_WINDOWS:
        out = ep_path(kind, wn)
        if os.path.exists(out + ".stats.json"):
            continue
        w = r4.win(wn)
        hi = "2026-07-02" if wn == "w0" else w.hi            # как `tk604_run.cmd_ep`
        d = U.wdir(wn)
        r4.run([sys.executable, os.path.join(HERE, "tk654_episodes.py"), "--t0", w.t0, "--eng", U.eng(wn), "--cache", d + "/cache", "--trades", d + "/aggtrades",
                "--lo", w.lo, "--hi", hi, "--syms", sf, "--out", out, "--procs", str(a.procs)], {"SIGFEAT_BASE_DATE": w.t0, "SIGFEAT_DAYS": "80"}, qq("log", "ep-%s-%s.log" % (kind, wn)))
        say("ep %s %s: %s" % (kind, wn, open(out + ".stats.json", encoding="utf-8").read().replace("\n", " ")))


def ep_rows(path, keep=None):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if keep is None or r["sym"] in keep]


def cmd_gep(a):
    keep = set(gate_coins())
    cols = [c for c in tf.EP_COLS if c != "in_pool"]
    res, bad = {}, []
    for wn in st.ALLOWED_WINDOWS:
        mine = sorted(tuple(r[c] for c in cols) for r in ep_rows(ep_path("gate", wn)))
        ref = sorted(tuple(r[c] for c in cols) for r in ep_rows(os.path.join(EP604, wn + ".csv"), keep))
        eq = mine == ref
        res[wn] = dict(rows=len(mine), rows_tk604=len(ref), equal=eq)
        if not eq:
            bad.append("%s: строк %d / %d, различных %d" % (wn, len(mine), len(ref), len(set(mine) ^ set(ref))))
    r4.write_json(qq("gates", "gep.json"), dict(coins=sorted(keep), res=res, bad=bad))
    say("G-ep", "FAIL " + "; ".join(bad) if bad else "PASS: монеты %s, %s" % (",".join(sorted(keep)), json.dumps(res, ensure_ascii=False)))
    if bad:
        sys.exit(1)


def episodes_h775():
    """Склейка эпизодов h775 по окнам: TK-604 (566 общих) + новые монеты (`ep/new-<окно>.csv`); пересечения монет нет (охрана)."""
    new = set(new_coins())
    per = {}
    for w in st.HIST_WINDOWS:
        old_rows = ep_rows(os.path.join(EP604, w.name + ".csv"))
        new_rows = ep_rows(ep_path("new", w.name))
        if {r["sym"] for r in new_rows} - new or {r["sym"] for r in old_rows} & new:
            raise GuardError("эпизоды %s: монеты смешаны" % w.name)
        per[w.name] = tf.window_rows_to_arrays(old_rows + new_rows, w)
    return tf.splice_episodes(per)


# ---- таблицы признаков кандидатов --------------------------------------------------------------------------------------------

def load_window(w, side, hist):
    """Кандидаты окна (все строки дампа базы, как `tk634/feat`): ключи, `mv5`, `tpm60` (таблица TK-634), `pierce_ep`, `spread_tau`, `cons60`, `zv6h` (эпизоды f12), `n`, `k` (`bk`)."""
    import pandas as pd
    sym, ms, raw = rfx.read_ftab(os.path.join(FEAT34, "h775", "%s-%s.csv" % (w.name, side)))
    eps = pd.read_parquet(os.path.join(FEAT34, "h775", "%s-%s" % (w.name, side), "eps-%s.parquet" % side), columns=["sym", "sig_ms"] + list(EPS_EXTRA))
    if eps.duplicated(["sym", "sig_ms"]).any():
        raise GuardError("%s-%s: дубль (sym, sig_ms) в эпизодах f12" % (w.name, side))
    key = pd.DataFrame(dict(sym=list(sym), sig_ms=ms))
    m = key.merge(eps, on=["sym", "sig_ms"], how="left", validate="one_to_one")
    t = dict(sym=np.asarray(sym, dtype=object), ms=ms, mv5=raw["mv5"], tpm60=raw["tpm60"])
    for c in ("pierce_ep", "spread_tau", "cons60", "zv6h"):
        t[c] = m[c].to_numpy(np.float64)
    mv5_e = m["mv5"].to_numpy(np.float64)
    a_, b_ = raw["mv5"], mv5_e
    t["gkey_mv5_bad"] = int((~((a_ == b_) | (np.isnan(a_) & np.isnan(b_)))).sum())
    t["n"], t["k"], _ = tf.features(hist, t["sym"], ms + w.t0_ms)
    return t


def compute_thresholds(tabs):
    thr = {}
    for fam, col in LADDERS + (("MV5_50", "mv5"),):
        v = np.concatenate([tabs[w.name][col][in_scored_range(w, tabs[w.name]["ms"]) & np.isfinite(tabs[w.name][col])] for w in st.TRADE_WINDOWS])
        qs = (0.5,) if fam == "MV5_50" else QS
        thr[fam] = dict(n=int(len(v)), q={"%.2f" % p: sig2(float(np.quantile(v, p, method="linear"))) for p in qs},
                        q_raw={"%.2f" % p: float(np.quantile(v, p, method="linear")) for p in qs})
    return thr


def cell_defs(thr):
    """Все клетки п. 5 до слияния дублей: [{cid, kind, label, …}]."""
    cells = []
    names = dict(PE="проколов уровня", ST="разброс пивотов, τ", MV5="ход за 5 мин, NATR", CN60="минут проторговки за 60", ZV6="доля пустых минут за 6 ч", TPM60="сделок в минуту за 60 мин")
    for fam, col in LADDERS:
        for d in ("ge", "le"):
            for p in QS:
                x = thr[fam]["q"]["%.2f" % p]
                cells.append(dict(cid="%s_%s_q%02d" % (fam, d, round(p * 100)), kind="lad", col=col, dir=d, X=x,
                                  label="%s %s %s (q%d)" % (names[fam], "≥" if d == "ge" else "≤", ("%g" % x).replace(".", ","), round(p * 100))))
    x5 = thr["MV5_50"]["q"]["0.50"]
    cells += [dict(cid="CLEAN", kind="named", label="чистый уровень: проколов 0"), dict(cid="ZONE", kind="named", label="зона: проколов ≥ 1"),
              dict(cid="NOCONS", kind="named", label="без проторговки: cons60 = 0"),
              dict(cid="SHARP", kind="named", X=x5, label="резкий без проторговки: cons60 = 0 и mv5 ≥ %s" % ("%g" % x5).replace(".", ",")),
              dict(cid="DENSE", kind="named", label="не разреженный: zv6h = 0")]
    for K, tau in st.GRID:
        cells.append(dict(cid=st.cell_id(K, tau), kind="bk", K=K, tau=tau, label="пробиваемость: K = %d, bk ≥ %s" % (K, ("%g" % tau).replace(".", ","))))
    return cells


def _bad(v, cond):
    """Исключён: значение определено и условие «пропустить» не выполнено (пусто проходит)."""
    with np.errstate(invalid="ignore"):
        return np.isfinite(v) & ~cond(v)


def excluded(c, t):
    if c["kind"] == "lad":
        v = t[c["col"]]
        return _bad(v, (lambda x: x >= c["X"]) if c["dir"] == "ge" else (lambda x: x <= c["X"]))
    if c["kind"] == "bk":
        return tf.excluded(t["n"], t["k"], c["K"], c["tau"])
    cid = c["cid"]
    if cid == "CLEAN":
        return _bad(t["pierce_ep"], lambda x: x == 0)
    if cid == "ZONE":
        return _bad(t["pierce_ep"], lambda x: x >= 1)
    if cid == "NOCONS":
        return _bad(t["cons60"], lambda x: x == 0)
    if cid == "DENSE":
        return _bad(t["zv6h"], lambda x: x == 0)
    if cid == "SHARP":
        cn, mv = t["cons60"], t["mv5"]
        ok = np.isfinite(cn) & np.isfinite(mv)
        with np.errstate(invalid="ignore"):
            return ok & ~((cn == 0) & (mv >= c["X"]))
    raise GuardError("клетка %s" % cid)


def merge_dups(cells, tabs):
    """Слияние по множеству исключённых строк таблиц трёх окон; пустое множество — «≡ база» (п. 4)."""
    out, by, same_base = [], {}, []
    for c in cells:
        mk = np.concatenate([excluded(c, tabs[w.name]) for w in st.TRADE_WINDOWS])
        if not mk.any():
            same_base.append(c["cid"])
            continue
        key = mk.tobytes()
        if key in by:
            out[by[key]]["also"].append(c["cid"])
            continue
        by[key] = len(out)
        out.append(dict(c, also=[]))
    return out, same_base


def cells_path(side):
    return qq("res", "cells54-def-%s.json" % side)


def ftab_path(wname, side):
    return qq("ftab54", "%s-%s.csv" % (wname, side))


def cmd_counts(a):
    eps = episodes_h775()
    for side in SIDES:
        hist = tf.episode_history(eps, tf.SIDE_NAME[side])
        tabs = {w.name: load_window(w, side, hist) for w in st.TRADE_WINDOWS}
        gk = {w: tabs[w]["gkey_mv5_bad"] for w in tabs}
        if any(gk.values()):
            raise GuardError("G-key %s: mv5 эпизодов f12 ≠ таблице TK-634: %s" % (side, gk))
        thr = compute_thresholds(tabs)
        cells, same_base = merge_dups(cell_defs(thr), tabs)
        W, S, R = r34.trade_rows(U, side)
        idx = {w.name: {(x, int(r)): i for i, (x, r) in enumerate(zip(tabs[w.name]["sym"], tabs[w.name]["ms"]))} for w in st.TRADE_WINDOWS}
        cols = ("pierce_ep", "spread_tau", "mv5", "cons60", "zv6h", "tpm60", "n", "k")
        tr = {c: np.full(len(W), np.nan) for c in cols}
        for w in st.TRADE_WINDOWS:
            sel = np.nonzero(W == w.name)[0]
            pos = np.array([idx[w.name].get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
            if (pos < 0).any():
                raise GuardError("%s %s: %d сделок базы без строки таблицы кандидатов" % (side, w.name, int((pos < 0).sum())))
            for c in cols:
                tr[c][sel] = tabs[w.name][c][pos]
        tr["n"], tr["k"] = tr["n"].astype(np.int64), tr["k"].astype(np.int64)
        n_t = len(W)
        say("== h775 %s: сделок базы %d; G-key mv5: расхождений 0" % (side, n_t))
        dist = {}
        for c in cols[:6]:
            allc = np.concatenate([tabs[w.name][c][in_scored_range(w, tabs[w.name]["ms"])] for w in st.TRADE_WINDOWS])
            fin = allc[np.isfinite(allc)]
            dist[c] = dict(cand_scored=int(len(allc)), cand_nan=int((~np.isfinite(allc)).sum()), trades_nan=int((~np.isfinite(tr[c])).sum()),
                           share_zero=float((fin == 0).mean()), q05_25_50_75_95=[float(x) for x in np.quantile(fin, [0.05, 0.25, 0.5, 0.75, 0.95])])
            say("   %-10s кандидаты: пусто %d из %d; сделок базы пусто %d; доля нулей %.3f; квантили 5/25/50/75/95 %s" % (
                c, dist[c]["cand_nan"], len(allc), dist[c]["trades_nan"], dist[c]["share_zero"], [float("%.4g" % x) for x in dist[c]["q05_25_50_75_95"]]))
        cov = {K: int((tr["n"] >= K).sum()) for K in st.GRID_K}
        say("   bk: сделок базы с n ≥ K: %s; по месяцам (n ≥ 20) — в отчёте" % cov)
        for fam in thr:
            say("   пороги %s (по %d сигналам): %s" % (fam, thr[fam]["n"], ", ".join("q%02d → %g" % (round(float(k) * 100), v) for k, v in thr[fam]["q"].items())))
        res = dict(uni="h775", side=side, n_trades=n_t, dist=dist, bk_cov=cov, thresholds=thr, same_as_base=same_base, cells=[])
        for c in cells:
            exc_c = n_c = 0
            for w in st.TRADE_WINDOWS:
                sc = in_scored_range(w, tabs[w.name]["ms"])
                exc_c += int((excluded(c, tabs[w.name]) & sc).sum())
                n_c += int(sc.sum())
            ex_t = excluded(c, tr)
            row = {k: v for k, v in c.items()}
            row.update(cand_excluded=exc_c, cand_rows=n_c, trades_excluded=int(ex_t.sum()), trades_remain=int((~ex_t).sum()))
            res["cells"].append(row)
            say("   %-14s %-48s: исключено кандидатов %d из %d (%.1f %%), сделок базы %d из %d, остаётся %d%s%s" % (
                c["cid"], c["label"], exc_c, n_c, 100 * exc_c / n_c, int(ex_t.sum()), n_t, int((~ex_t).sum()),
                "  [мало сделок]" if int((~ex_t).sum()) < MIN_TRADES else "", ("  = " + ", ".join(c["also"])) if c["also"] else ""))
        say("   ≡ база (ничего не исключают): %s" % (", ".join(same_base) or "нет"))
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            cols_t = {c["cid"]: np.where(excluded(c, t), 0.0, 1.0) for c in cells}
            with open(ftab_path(w.name, side), "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols_t) + "\n")
                for i in range(len(t["sym"])):
                    f.write("%s,%d,%s\n" % (t["sym"][i], int(t["ms"][i]), ",".join("%.1f" % cols_t[k][i] for k in cols_t)))
            say("   ftab54 %s-%s: %d строк, клеток %d" % (w.name, side, len(t["sym"]), len(cols_t)))
        r4.write_json(cells_path(side), res)
    say("counts готово — раздел «Счёт без P&L» до чтения P&L")


COMMANDS = {"gu0": cmd_gu0, "ep": cmd_ep, "gep": cmd_gep, "counts": cmd_counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
