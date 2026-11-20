#!/usr/bin/env python3
"""П-42 §13.36 (TK-638): титровка оборота D × H и числа сделок n60 / n24 — счёт без P&L (`counts`).
  python3 tk638_run.py counts --uni h775|o566   — общие пороги N (h775 первым пишет res/thr38.json, o566 берёт те же абсолютные N), доли исключённых, остаток сделок базы по клеткам, таблицы `--ftab` лестниц
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
import tk638_feat as ft  # noqa: E402

ROOT = "/data/p42-tk638"
SIDES = st.SIDES
D_LEVELS = (12, 25, 50, 100, 200, 300, 500)                      # п. 5: суточный оборот, млн $
H_LEVELS = (0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0, 5.0)       # часовой оборот, млн $
QS = (0.2, 0.4, 0.6, 0.8)
LADDERS = (("N60", "n60", "m60"), ("N24", "n24", "m24"))
MIN_TRADES = 70                                                  # п. 5: «мало сделок» при n < 70 за всё окно
FEAT34 = ft.FEAT34


class GuardError(RuntimeError):
    pass


def say(*a):
    print(*a, flush=True)


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def sub(u, name):
    return name if u.name == "o566" else name + "-" + u.name


def sig2(x):
    """Округление до 2 значащих цифр (п. 4)."""
    return float("%.2g" % x)


def cell_id(d, h):
    return "D%g_H%g" % (d, h)


def in_scored_range(w, rel_ms):
    """Сигнал в зачётном диапазоне окна (по сутки сигнала, включительно): кандидаты окон перекрываются — таблицы признаков идут с 14-х по 73-е сутки от T0 окна, зачёт — [lo, hi]."""
    d = st.abs_day(w, rel_ms)
    return (d >= (w.lod - st.D0).days) & (d <= (w.hid - st.D0).days)


def load_window(u, w, side):
    """Кандидаты окна: ключи и столбцы признаков f12 (turn1h / turn24h) + выход tcount (n60, n24, m60, m24); охрана порядка строк."""
    sym, ms, raw = rfx.read_ftab(ft.feat_path(FEAT34, u.name, w.name, side))
    csym, c = ft.read_counts(ft.feat_path(os.path.join(ROOT, "feat"), u.name, w.name, side))
    if list(sym) != list(csym) or not np.array_equal(ms, c["sig_ms"]):
        raise GuardError("%s %s-%s: ключи tcount ≠ таблица признаков f12" % (u.name, w.name, side))
    return dict(sym=sym, ms=ms, tpm60=raw["tpm60"], turn1h=raw["turn1h"], turn24h=raw["turn24h"], n60=c["n60"], n24=c["n24"], m60=c["m60"], m24=c["m24"])


def thr_path():
    return q("res", "thr38.json")


def compute_thresholds(tabs):
    """Общие абсолютные пороги N: объединённый квантиль (linear) по сигналам базы h775 стороны в зачётных диапазонах трёх окон (n определено), 2 значащие цифры.
    Для справки — то же по всем строкам таблиц (с перекрытием окон)."""
    thr = {}
    for lad, col, mcol in LADDERS:
        v_scored, v_all = [], []
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            ok = ~t[mcol]
            sc = in_scored_range(w, t["ms"])
            v_scored.append(t[col][ok & sc])
            v_all.append(t[col][ok])
        v_scored, v_all = np.concatenate(v_scored).astype(float), np.concatenate(v_all).astype(float)
        thr[lad] = dict(n_scored=int(len(v_scored)), n_all_rows=int(len(v_all)), q={}, q_raw={}, q_all_rows_rounded={})
        for p in QS:
            key = "%.2f" % p
            raw_v = float(np.quantile(v_scored, p, method="linear"))
            thr[lad]["q_raw"][key] = raw_v
            thr[lad]["q"][key] = sig2(raw_v)
            thr[lad]["q_all_rows_rounded"][key] = sig2(float(np.quantile(v_all, p, method="linear")))
    return thr


def ladder_cells(thr):
    """Клетки лестниц: {cid: (лестница, N, [p])}; совпавшие после округления пороги — одна клетка (дубль), в подписи оба p."""
    cells, by_n = [], {}
    for lad, col, mcol in LADDERS:
        for p in QS:
            n = thr[lad]["q"]["%.2f" % p]
            key = (lad, n)
            if key in by_n:
                cells[by_n[key]]["ps"].append(p)
                continue
            by_n[key] = len(cells)
            cells.append(dict(cid="%s_q%02d" % (lad, round(p * 100)), lad=lad, col=col, mcol=mcol, N=n, ps=[p]))
    return cells


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
        cells = ladder_cells(thr)
        # сделки базы: ключи (окно, монета, мс сигнала от T0) в порядке `r5.load_series`; P&L не читается
        W, S, R = r34.trade_rows(u, side)
        idx = {w.name: {(x, int(r)): i for i, (x, r) in enumerate(zip(tabs[w.name]["sym"], tabs[w.name]["ms"]))} for w in st.TRADE_WINDOWS}
        cols = ("turn1h", "turn24h", "n60", "n24", "m60", "m24")
        tr = {c: np.full(len(W), np.nan) for c in cols}
        for w in st.TRADE_WINDOWS:
            sel = np.nonzero(W == w.name)[0]
            pos = np.array([idx[w.name].get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
            if (pos < 0).any():
                raise GuardError("%s %s %s: %d сделок базы без строки таблицы кандидатов" % (u.name, side, w.name, int((pos < 0).sum())))
            for c in cols:
                tr[c][sel] = tabs[w.name][c][pos]
        n_t = len(W)
        say("== %s %s: сделок базы %d; кандидатов в зачётных диапазонах %d, всех строк таблиц (окна перекрываются) %d" % (
            u.name, side, n_t, sum(int(in_scored_range(w, tabs[w.name]["ms"]).sum()) for w in st.TRADE_WINDOWS), sum(len(tabs[w.name]["sym"]) for w in st.TRADE_WINDOWS)))
        allc = {c: np.concatenate([tabs[w.name][c][in_scored_range(w, tabs[w.name]["ms"])] for w in st.TRADE_WINDOWS]).astype(float) for c in ("n60", "n24", "tpm60")}
        say("   кандидаты (зачётные диапазоны): n60 квантили 5/25/50/75/95 %s, среднее %.1f; n24 %s, среднее %.0f; контекст tpm60·60 (другое окно, п. 7): среднее %.1f" % (
            [int(x) for x in np.quantile(allc["n60"], [0.05, 0.25, 0.5, 0.75, 0.95])], allc["n60"].mean(), [int(x) for x in np.quantile(allc["n24"], [0.05, 0.25, 0.5, 0.75, 0.95])], allc["n24"].mean(),
            (allc["tpm60"] * 60).mean()))
        if u.name == "h775":
            for lad, _, _ in LADDERS:
                say("   пороги %s (общий квантиль по %d сигналам; в скобках — по всем строкам таблиц): %s" % (
                    lad, thr[lad]["n_scored"], ", ".join("q%02d → %g (%g)" % (round(float(k) * 100), v, thr[lad]["q_all_rows_rounded"][k]) for k, v in thr[lad]["q"].items())))
        res = dict(uni=u.name, side=side, n_trades=n_t, ladders=[], pool=[], missing=dict(trades_m60=int(tr["m60"].sum()), trades_m24=int(tr["m24"].sum()),
                   cand_m60=sum(int(tabs[w]["m60"].sum()) for w in tabs), cand_m24=sum(int(tabs[w]["m24"].sum()) for w in tabs)))
        for c in cells:
            excl_c = n_ex_c = 0
            for w in st.TRADE_WINDOWS:
                t = tabs[w.name]
                sc = in_scored_range(w, t["ms"])
                ex = ~t[c["mcol"]] & (t[c["col"]] < c["N"]) & sc
                excl_c += int(ex.sum())
                n_ex_c += int(sc.sum())
            ex_t = ~tr[c["mcol"]].astype(bool) & (tr[c["col"]] < c["N"])
            res["ladders"].append(dict(cid=c["cid"], lad=c["lad"], N=c["N"], ps=c["ps"], cand_excluded=excl_c, cand_rows=n_ex_c, cand_share=round(excl_c / n_ex_c, 4),
                                       trades_excluded=int(ex_t.sum()), trades_remain=int((~ex_t).sum()), trades_share=round(float(ex_t.mean()), 4)))
            say("   %-8s ≥ %g сделок за %s (p = %s): исключено кандидатов зачётных диапазонов %d из %d (%.1f %%), сделок базы %d из %d (%.1f %%), остаётся %d%s" % (
                c["cid"], c["N"], "60 мин" if c["lad"] == "N60" else "24 ч", "/".join("%.1f" % p for p in c["ps"]), excl_c, n_ex_c, 100 * excl_c / n_ex_c, int(ex_t.sum()), n_t, 100 * float(ex_t.mean()),
                int((~ex_t).sum()), "  [мало сделок]" if int((~ex_t).sum()) < MIN_TRADES else ""))
        # остаток сделок базы по клеткам D × H (H ≥ 0,5): сделки базы, у которых оборот на сигнале ≥ D и ≥ H (верхняя оценка пула: пул ≠ подмножество базы, п. 7 G-pool)
        t1, t24 = tr["turn1h"], tr["turn24h"]
        res["turn_nan"] = dict(turn1h=int(np.isnan(t1).sum()), turn24h=int(np.isnan(t24).sum()))
        for d in D_LEVELS:
            row = []
            for h in H_LEVELS:
                if h < 0.5:
                    continue
                keep = np.isfinite(t1) & np.isfinite(t24) & (t1 >= h * 1e6) & (t24 >= d * 1e6)
                res["pool"].append(dict(cid=cell_id(d, h), D=d, H=h, remain=int(keep.sum())))
                row.append("%g:%d%s" % (h, int(keep.sum()), "*" if int(keep.sum()) < MIN_TRADES else ""))
            say("   остаток сделок базы D=%-3g | H: %s" % (d, "  ".join(row)))
        res["note"] = "остаток по D × H — сделки базы с turn1h ≥ H и turn24h ≥ D (верхняя оценка клетки пула); * — меньше %d сделок" % MIN_TRADES
        # таблицы `--ftab` лестниц: 0,0 — исключён (n < N), 1,0 — пропущен; пусто (m = 1) проходит
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            cols_t = {c["cid"]: np.where(~t[c["mcol"]] & (t[c["col"]] < c["N"]), 0.0, 1.0) for c in cells}
            path = q(sub(u, "ftab38"), "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols_t) + "\n")
                for i in range(len(t["sym"])):
                    f.write("%s,%d,%s\n" % (t["sym"][i], int(t["ms"][i]), ",".join("%.1f" % cols_t[k][i] for k in cols_t)))
            say("   ftab38 %s %s-%s: %d строк, клеток %d" % (u.name, w.name, side, len(t["sym"]), len(cols_t)))
        json.dump(res, open(q("res", "counts38-%s-%s.json" % (u.name, side)), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if u.name == "h775":
        json.dump(side_res, open(thr_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("пороги записаны:", thr_path())
    say("counts %s готово — запись в протокол §13.36 п. 11 до чтения P&L" % u.name)


COMMANDS = {"counts": cmd_counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--uni", default="h775", choices=("h775", "o566"))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
