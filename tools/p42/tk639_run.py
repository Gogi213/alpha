#!/usr/bin/env python3
"""П-42 §13.37 (TK-639): число сделок за 6 / 12 ч и средний размер сделки (оборот / число сделок за 1 / 6 / 24 ч) — счёт без P&L (`counts`).
  python3 tk639_run.py counts --uni h775|o566   — общие пороги (h775 первым пишет res/thr39.json, o566 берёт те же абсолютные числа), доли исключённых, остаток сделок базы по клеткам, таблицы `--ftab` лестниц
P&L не печатается и не считается: из файла сделок читаются только ключи (окно, монета, мс сигнала) — `tk634_run.trade_rows`."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import retitr_ftab as rfx  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_run as t8  # noqa: E402
import tk639_feat as ft  # noqa: E402

ROOT = "/data/p42-tk639"
SIDES = st.SIDES
QS = (0.2, 0.4, 0.6, 0.8)
MIN_TRADES = 70                                                  # п. 5: «мало сделок» при n < 70 за всё окно
# (семейство, столбец величины, столбец флага «нет файла суток», направления): число сделок — только «≥»; средний размер — «≥» и «≤»
FAMILIES = (("NC360", "n360", "m360", ("ge",)), ("NC720", "n720", "m720", ("ge",)),
            ("SZ60", "size60", "m60", ("ge", "le")), ("SZ360", "size360", "m360", ("ge", "le")), ("SZ1440", "size1440", "m1440", ("ge", "le")))
GuardError = t8.GuardError
say, sub, sig2 = t8.say, t8.sub, t8.sig2
in_scored_range = t8.in_scored_range


def qq(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def load_window(u, w, side):
    """Кандидаты окна: ключи и столбцы f12 (turn1h / turn24h) + выход tsize (n, v, m по окнам) + размеры (NaN — не определён: нет файла суток или n = 0; пусто проходит)."""
    sym, ms, raw = rfx.read_ftab(ft.feat_path(ft.FEAT34, u.name, w.name, side))
    csym, c = ft.read_sizes(ft.feat_path(os.path.join(ROOT, "feat"), u.name, w.name, side))
    if list(sym) != list(csym) or not np.array_equal(ms, c["sig_ms"]):
        raise GuardError("%s %s-%s: ключи tsize ≠ таблица признаков f12" % (u.name, w.name, side))
    t = dict(sym=sym, ms=ms, turn1h=raw["turn1h"], turn24h=raw["turn24h"], **c)
    for k in (60, 360, 1440):
        n = c["n%d" % k]
        with np.errstate(divide="ignore", invalid="ignore"):
            t["size%d" % k] = np.where(~c["m%d" % k] & (n > 0), c["v%d" % k] / np.maximum(n, 1), np.nan)
    return t


def thr_path():
    return qq("res", "thr39.json")


def defined(t, col, mcol):
    """Величина определена: файлы суток есть; у размера — ещё n > 0 (NaN вне)."""
    v = t[col].astype(float)
    return ~t[mcol] & np.isfinite(v)


def compute_thresholds(tabs):
    """Общие абсолютные пороги: объединённый квантиль (linear) по сигналам базы h775 стороны в зачётных диапазонах трёх окон (величина определена), 2 значащие цифры; справка — по всем строкам таблиц."""
    thr = {}
    for fam, col, mcol, _ in FAMILIES:
        v_scored, v_all = [], []
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            ok = defined(t, col, mcol)
            sc = in_scored_range(w, t["ms"])
            v_scored.append(t[col][ok & sc].astype(float))
            v_all.append(t[col][ok].astype(float))
        v_scored, v_all = np.concatenate(v_scored), np.concatenate(v_all)
        thr[fam] = dict(n_scored=int(len(v_scored)), n_all_rows=int(len(v_all)), q={}, q_raw={}, q_all_rows_rounded={})
        for p in QS:
            key = "%.2f" % p
            raw_v = float(np.quantile(v_scored, p, method="linear"))
            thr[fam]["q_raw"][key] = raw_v
            thr[fam]["q"][key] = sig2(raw_v)
            thr[fam]["q_all_rows_rounded"][key] = sig2(float(np.quantile(v_all, p, method="linear")))
    return thr


def cell_defs(thr):
    """Клетки: [{cid, fam, col, mcol, dir, N, ps}]; совпавшие после округления пороги одного направления — одна клетка (дубль), в подписи оба p."""
    cells, by = [], {}
    for fam, col, mcol, dirs in FAMILIES:
        for d in dirs:
            for p in QS:
                n = thr[fam]["q"]["%.2f" % p]
                key = (fam, d, n)
                if key in by:
                    cells[by[key]]["ps"].append(p)
                    continue
                by[key] = len(cells)
                cid = "%s_q%02d" % (fam, round(p * 100)) if fam.startswith("NC") else "%s_%s_q%02d" % (fam, d, round(p * 100))
                cells.append(dict(cid=cid, fam=fam, col=col, mcol=mcol, dir=d, N=n, ps=[p]))
    return cells


def excluded(c, t):
    """Маска «исключён» по таблице значений: величина определена и нарушает порог (пусто проходит)."""
    ok = defined(t, c["col"], c["mcol"])
    v = t[c["col"]].astype(float)
    with np.errstate(invalid="ignore"):
        bad = (v < c["N"]) if c["dir"] == "ge" else (v > c["N"])
    return ok & bad


def label(c):
    what = {"NC360": "сделок за 6 ч", "NC720": "сделок за 12 ч", "SZ60": "размер сделки за 1 ч", "SZ360": "размер сделки за 6 ч", "SZ1440": "размер сделки за 24 ч"}[c["fam"]]
    sign = "≤" if c["dir"] == "le" else "≥"
    num = ("{:,}".format(int(c["N"])).replace(",", " ")) if c["fam"].startswith("NC") or c["N"] >= 100 else ("%g" % c["N"]).replace(".", ",")
    unit = "" if c["fam"].startswith("NC") else " $"
    return "%s %s %s%s (q%s)" % (what, sign, num, unit, "/".join("%d" % round(p * 100) for p in c["ps"]))


def cmd_counts(a):
    u = r5.Uni(a.uni)
    side_res = {}
    for side in SIDES:
        tabs = {w.name: load_window(u, w, side) for w in st.TRADE_WINDOWS}
        if u.name == "h775":
            thr = compute_thresholds(tabs)
            side_res[side] = thr
        else:
            thr = json.load(open(thr_path(), encoding="utf-8"))[side]
        cells = cell_defs(thr)
        W, S, R = r34.trade_rows(u, side)
        idx = {w.name: {(x, int(r)): i for i, (x, r) in enumerate(zip(tabs[w.name]["sym"], tabs[w.name]["ms"]))} for w in st.TRADE_WINDOWS}
        cols = ("n360", "n720", "size60", "size360", "size1440", "m60", "m360", "m720", "m1440")
        tr = {c: np.full(len(W), np.nan) for c in cols}
        for w in st.TRADE_WINDOWS:
            sel = np.nonzero(W == w.name)[0]
            pos = np.array([idx[w.name].get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
            if (pos < 0).any():
                raise GuardError("%s %s %s: %d сделок базы без строки таблицы кандидатов" % (u.name, side, w.name, int((pos < 0).sum())))
            for c in cols:
                tr[c][sel] = tabs[w.name][c][pos]
        trt = {c: (tr[c].astype(bool) if c.startswith("m") else tr[c]) for c in cols}
        n_t = len(W)
        n_scored = sum(int(in_scored_range(w, tabs[w.name]["ms"]).sum()) for w in st.TRADE_WINDOWS)
        say("== %s %s: сделок базы %d; кандидатов в зачётных диапазонах %d, всех строк таблиц (окна перекрываются) %d" % (u.name, side, n_t, n_scored, sum(len(tabs[w.name]["sym"]) for w in st.TRADE_WINDOWS)))
        dist = {}
        for fam, col, mcol, _ in FAMILIES:
            allc = np.concatenate([tabs[w.name][col][in_scored_range(w, tabs[w.name]["ms"]) & defined(tabs[w.name], col, mcol)].astype(float) for w in st.TRADE_WINDOWS])
            n_undef = sum(int((in_scored_range(w, tabs[w.name]["ms"]) & ~defined(tabs[w.name], col, mcol)).sum()) for w in st.TRADE_WINDOWS)
            dist[fam] = dict(q05_25_50_75_95=[float(x) for x in np.quantile(allc, [0.05, 0.25, 0.5, 0.75, 0.95])], mean=float(allc.mean()), n=int(len(allc)), n_undefined=n_undef)
            say("   %-6s кандидаты (зачётные): квантили 5/25/50/75/95 %s, среднее %.1f; не определено (пусто проходит) %d" % (fam, [float("%.4g" % x) for x in dist[fam]["q05_25_50_75_95"]], allc.mean(), n_undef))
        if u.name == "h775":
            for fam, _, _, _ in FAMILIES:
                say("   пороги %s (общий квантиль по %d сигналам; в скобках — по всем строкам таблиц): %s" % (
                    fam, thr[fam]["n_scored"], ", ".join("q%02d → %g (%g)" % (round(float(k) * 100), v, thr[fam]["q_all_rows_rounded"][k]) for k, v in thr[fam]["q"].items())))
        res = dict(uni=u.name, side=side, n_trades=n_t, dist=dist, ladders=[],
                   missing=dict(trades=int(trt["m1440"].sum()), cand={k: sum(int(tabs[w][k].sum()) for w in tabs) for k in ("m60", "m360", "m720", "m1440")}),
                   spearman={})
        # справка: ранговая связь размеров с n24 и turn24h (кандидаты зачётных диапазонов; не P&L)
        for fam, col, mcol, _ in FAMILIES:
            if not fam.startswith("SZ"):
                continue
            x = np.concatenate([tabs[w.name][col][in_scored_range(w, tabs[w.name]["ms"]) & defined(tabs[w.name], col, mcol)].astype(float) for w in st.TRADE_WINDOWS])
            n24 = np.concatenate([tabs[w.name]["n1440"][in_scored_range(w, tabs[w.name]["ms"]) & defined(tabs[w.name], col, mcol)].astype(float) for w in st.TRADE_WINDOWS])
            t24 = np.concatenate([tabs[w.name]["turn24h"][in_scored_range(w, tabs[w.name]["ms"]) & defined(tabs[w.name], col, mcol)].astype(float) for w in st.TRADE_WINDOWS])
            res["spearman"][fam] = dict(rho_vs_n24=spearman(x, n24), rho_vs_turn24h=spearman(x, t24))
        for c in cells:
            excl_c = n_ex_c = 0
            for w in st.TRADE_WINDOWS:
                t = tabs[w.name]
                sc = in_scored_range(w, t["ms"])
                excl_c += int((excluded(c, t) & sc).sum())
                n_ex_c += int(sc.sum())
            ex_t = excluded(c, trt)
            res["ladders"].append(dict(cid=c["cid"], fam=c["fam"], dir=c["dir"], N=c["N"], ps=c["ps"], cand_excluded=excl_c, cand_rows=n_ex_c, cand_share=round(excl_c / n_ex_c, 4),
                                       trades_excluded=int(ex_t.sum()), trades_remain=int((~ex_t).sum()), trades_share=round(float(ex_t.mean()), 4)))
            say("   %-14s %-44s: исключено кандидатов зачётных диапазонов %d из %d (%.1f %%), сделок базы %d из %d (%.1f %%), остаётся %d%s" % (
                c["cid"], label(c), excl_c, n_ex_c, 100 * excl_c / n_ex_c, int(ex_t.sum()), n_t, 100 * float(ex_t.mean()), int((~ex_t).sum()), "  [мало сделок]" if int((~ex_t).sum()) < MIN_TRADES else ""))
        # таблицы `--ftab` лестниц: 0,0 — исключён, 1,0 — пропущен; пусто (не определено) проходит
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            cols_t = {c["cid"]: np.where(excluded(c, t), 0.0, 1.0) for c in cells}
            path = qq(sub(u, "ftab39"), "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols_t) + "\n")
                for i in range(len(t["sym"])):
                    f.write("%s,%d,%s\n" % (t["sym"][i], int(t["ms"][i]), ",".join("%.1f" % cols_t[k][i] for k in cols_t)))
            say("   ftab39 %s %s-%s: %d строк, клеток %d" % (u.name, w.name, side, len(t["sym"]), len(cols_t)))
        json.dump(res, open(qq("res", "counts39-%s-%s.json" % (u.name, side)), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if u.name == "h775":
        json.dump(side_res, open(thr_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("пороги записаны:", thr_path())
    say("counts %s готово — запись в протокол §13.37 п. 11 до чтения P&L" % u.name)


def spearman(x, y):
    import pandas as pd
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


COMMANDS = {"counts": cmd_counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--uni", default="h775", choices=("h775", "o566"))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
