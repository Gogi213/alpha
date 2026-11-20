#!/usr/bin/env python3
"""П-42 §13.35 (TK-636): монета как признак — клетки (TOV_X, AGE, VOL, PEER, COUP_w_hi/lo, справочная CLS), таблицы `--ftab`, ступень 1, выбор по п. 6–8.
Подкоманды (порядок — п. 12 протокола): counts, step1, cellsall, g8, gu3, select. P&L читают только step1 / cellsall / g8 / gu3 / select — после PASS критика; `counts` P&L не читает.
Входы (готовые): признаки кандидатов `/data/p42-tk636/feat/<вс>/<окно>-<сторона>.csv` (`tk636_feat.py build`), сделки баз h775 / o566 (TK-621 / TK-625), прежние p Холма (select.json, select33.json, select34.json).
Композит связи COUP_w — среднее процентных рангов (середина ничьих) определённых компонент cb / ce / bt / cc по ECDF сделок базы h775 (≥ 3 из 4); клетка — «исключить значение вне диапазона»; в таблице `--ftab` — колонка 1 / 0."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_ftab as rfx  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_feat as f5  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk625_stats as s5  # noqa: E402
import tk633_run as r33  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk636_feat as ft  # noqa: E402

ROOT = "/data/p42-tk636"
SIDES = st.SIDES
ENG, BASE = r5.ENG, r5.BASE
say, write_json, sha256 = r4.say, r4.write_json, r4.sha256
GuardError = st.GuardError
SIDE_NAME_RU = {"L": "лонг", "S": "шорт"}
W_TRIAL, W_ALL, COMPS = ft.TRIAL_WINS, ft.WIN_ORDER, ft.COMP
MIN_COMP = 3                                       # п. 4: композит определён при ≥ 3 из 4 компонент
TOV_LEVELS = (25, 50, 100, 200, 400)               # п. 5: минимум суточного оборота, млн $ (часовой фиксирован — ключ базы t1h = 0,5 млн $)
Q_MAIN, Q_NB = 0.25, (0.10, 0.40)                  # п. 5: доля исключаемых (основная и соседи)
FV_PASS = 0.5                                      # движок: `fa=<клетка> fo=lo fv=0.5` — исключает значение 0,0, пропускает 1,0
N_OLD_TRIAL = 29                                   # 9 (TK-625) + 12 (TK-633) + 8 (TK-634), п. 6 (g1)
OLD_SELECT = ("/data/p42-tk625/res/select.json", "/data/p42-tk633/res/select33.json", "/data/p42-tk634/res/select34.json")
QS_AV = ("0.10", "0.25", "0.40", "0.60", "0.75", "0.90")
PEER_COL = {"PEER": "peer15", "PEER_w05": "peer5", "PEER_w60": "peer60"}
GROUPS = ("оборот (TOV)", "возраст (AGE)", "волатильность (VOL)", "связь с рынком (COUP)", "соседи по кластеру (PEER)")
_C = {}


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def sub(u, name):
    return name if u.name == "o566" else name + "-" + u.name


def uni_of(a):
    return r5.Uni(getattr(a, "uni", None) or "h775")


def code_id():
    """sha256 содержимого tk636_feat.py, tk636_run.py, tk634_run.py и tk625_{events,feat,run,stats}.py, tk633_run.py (12 знаков) — метка рядом с каждым числом отчёта."""
    import hashlib
    h = hashlib.sha256()
    for n in ("tk625_events.py", "tk625_feat.py", "tk625_run.py", "tk625_stats.py", "tk633_run.py", "tk634_run.py", "tk636_feat.py", "tk636_run.py"):
        h.update(open(os.path.join(HERE, n), "rb").read())
    return h.hexdigest()[:12]


def stamp(d):
    d["def"], d["code_id"] = f5.DEF_TAG, code_id()
    return d


def finite(x):
    x = np.asarray(x, dtype=float)
    return np.where(np.isfinite(x), x, np.nan)


# ---- композиты и пороги ------------------------------------------------------------------------------------------------------------

def composite(vals, refs, w):
    """COUP_w: среднее процентных рангов определённых компонент cb / ce / bt / cc окна w по ECDF сделок базы h775; определён при ≥ MIN_COMP определённых, иначе NaN (кандидат проходит)."""
    P = np.vstack([r34.ecdf_pct(refs["%s_%s" % (c, w)], vals["%s_%s" % (c, w)]) for c in COMPS])
    avail = np.isfinite(P).sum(axis=0)
    tot = np.nansum(P, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = tot / np.where(avail > 0, avail, 1)
    return np.where(avail >= MIN_COMP, m, np.nan)


def make_refs(raw):
    """Эталонные массивы ECDF: отсортированные определённые значения компонент (все окна, в т. ч. 7 сут.) по сделкам базы."""
    return {"%s_%s" % (c, w): np.sort(raw["%s_%s" % (c, w)][np.isfinite(raw["%s_%s" % (c, w)])]) for w in W_ALL for c in COMPS}


def derive(raw, refs):
    """Все значения на строках: сырые столбцы + композиты COUP_w (все пять окон)."""
    d = {k: finite(v) for k, v in raw.items()}
    for w in W_ALL:
        d["COUP_" + w] = composite(d, refs, w)
    return d


def quantile_table(d):
    """Пороги: квантили по сделкам базы h775 (определённые значения): AGE / VOL — 0,10 / 0,25 / 0,40; COUP_w — 0,10 … 0,90 (QS_AV)."""
    thr = {}
    for name, col in (("AGE", "age_d"), ("VOL", "natr_bp")):
        v = d[col][np.isfinite(d[col])]
        thr[name] = {k: float(np.quantile(v, float(k))) for k in ("0.10", "0.25", "0.40")}
    for w in W_ALL:
        v = d["COUP_" + w][np.isfinite(d["COUP_" + w])]
        thr["COUP_" + w] = {k: float(np.quantile(v, float(k))) for k in QS_AV}
    return thr


# ---- клетки ------------------------------------------------------------------------------------------------------------------------

def cell_specs():
    """Клетки стороны (п. 5): сначала все пробные T (порядок обхода дублей: TOV_25 … TOV_400, AGE, VOL, PEER, COUP15м_hi, COUP15м_lo, …), затем соседи N, затем справочная R (CLS).
    T: 5 + 3 + 8 = 16; N: 10 (TOV, соседние уровни) + 6 (AGE / VOL / PEER) + 16 (COUP) = 32; R: 1. Всего 49 на сторону."""
    T, N = [], []
    g = 0
    for x in TOV_LEVELS:
        g += 1
        T.append(dict(cid="TOV_%d" % x, kind="T", feat="TOV", x=x, share=None, group=g, fam="оборот (TOV)"))
        N.append(dict(cid="TOV_%d_dn" % x, kind="N", feat="TOV", x=x / 2.0, share=None, group=g, fam="оборот (TOV)"))
        N.append(dict(cid="TOV_%d_up" % x, kind="N", feat="TOV", x=x * 2.0, share=None, group=g, fam="оборот (TOV)"))
    for feat, fam in (("AGE", "возраст (AGE)"), ("VOL", "волатильность (VOL)")):
        g += 1
        T.append(dict(cid=feat, kind="T", feat=feat, x=None, share=Q_MAIN, group=g, fam=fam))
        for qq in Q_NB:
            N.append(dict(cid="%s_q%02d" % (feat, int(round(qq * 100))), kind="N", feat=feat, x=None, share=qq, group=g, fam=fam))
    g += 1
    T.append(dict(cid="PEER", kind="T", feat="PEER", x=None, share=None, group=g, fam="соседи по кластеру (PEER)"))
    for cid in ("PEER_w05", "PEER_w60"):
        N.append(dict(cid=cid, kind="N", feat="PEER", x=None, share=None, group=g, fam="соседи по кластеру (PEER)"))
    for w in W_TRIAL:
        for dirn in ("hi", "lo"):
            g += 1
            base = "COUP%s_%s" % (w, dirn)
            T.append(dict(cid=base, kind="T", feat="COUP", w=w, dirn=dirn, x=None, share=Q_MAIN, group=g, fam="связь с рынком (COUP)"))
            for qq in Q_NB:
                N.append(dict(cid="%s_q%02d" % (base, int(round(qq * 100))), kind="N", feat="COUP", w=w, dirn=dirn, x=None, share=qq, group=g, fam="связь с рынком (COUP)"))
    R = [dict(cid="CLS", kind="R", feat="CLS", x=None, share=None, group=99, fam="класс (справочно)")]
    return T + N + R


def excl_mask(sp, d, thr):
    """Исключённые строки (пусто проходит)."""
    f = sp["feat"]
    if f == "TOV":
        x = d["turn24h"]
        return np.isfinite(x) & (x < sp["x"] * 1e6)
    if f in ("AGE", "VOL"):
        x = d["age_d" if f == "AGE" else "natr_bp"]
        return np.isfinite(x) & (x < thr[f]["%.2f" % sp["share"]])
    if f == "PEER":
        x = d[PEER_COL[sp["cid"]]]
        return np.isfinite(x) & (x == 0)
    if f == "COUP":
        x = d["COUP_" + sp["w"]]
        t = thr["COUP_" + sp["w"]]
        if sp["dirn"] == "lo":
            return np.isfinite(x) & (x < t["%.2f" % sp["share"]])
        return np.isfinite(x) & (x > t["%.2f" % (1.0 - sp["share"])])
    if f == "CLS":
        x = d["cls"]
        return np.isfinite(x) & (x == 1)
    raise GuardError("клетка %r неизвестна" % sp["cid"])


# ---- чтение таблиц кандидатов и сделок -----------------------------------------------------------------------------------------

class Rows:
    """Строки таблицы признаков окна (окно, сторона): сырые столбцы `tk636_feat.py build`; ключ (монета, мс от T0)."""

    def __init__(self, u, wname, side):
        s, m, raw = rfx.read_ftab(os.path.join(ft.ROOT, "feat", u.name, "%s-%s.csv" % (wname, side)))
        self.sym, self.rel, self.raw = s, m, raw
        self.index = {(x, int(r)): i for i, (x, r) in enumerate(zip(s, m))}


def rows_of(u, wname, side):
    key = ("rows", u.name, wname, side)
    if key not in _C:
        _C[key] = Rows(u, wname, side)
    return _C[key]


def trade_kinds(u, side):
    """Вид выхода и P&L ($, gross − fees) в порядке `r34.trade_rows`."""
    key = ("kinds", u.name, side)
    if key in _C:
        return _C[key]
    K, P = [], []
    for w in st.TRADE_WINDOWS:
        t = st.read_trades_csv(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
        d = st.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
            K.append(t.kind[i])
            P.append(float(t.gross[i] - t.fees[i]))
    out = (np.array(K, dtype=object), np.array(P, dtype=float))
    _C[key] = out
    return out


def trade_values(u, side):
    """Значения признаков на сделках базы: ({столбец: массив}, ряд сделок, виды выхода, P&L). Охрана порядка: монета и время сигнала равны ряду `load_series`; P&L из файла сделок = val ряда."""
    key = ("tvals", u.name, side)
    if key in _C:
        return _C[key]
    W, S, R = r34.trade_rows(u, side)
    ser = r5.load_series(u, side, "base")
    t0 = {w.name: w.t0_ms for w in st.TRADE_WINDOWS}
    if list(ser.sym_name) != list(S) or not np.array_equal(ser.key_ms, R + np.array([t0[w] for w in W], dtype=np.int64)):
        raise GuardError("%s %s: порядок сделок базы не совпал с рядом load_series" % (u.name, side))
    kinds, pnl = trade_kinds(u, side)
    if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
        raise GuardError("%s %s: P&L сделок из файла ≠ val ряда" % (u.name, side))
    raw = {}
    for w in st.TRADE_WINDOWS:
        rows = rows_of(u, w.name, side)
        sel = np.nonzero(W == w.name)[0]
        pos = np.array([rows.index.get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
        if (pos < 0).any():
            raise GuardError("%s %s %s: %d сделок базы без строки таблицы признаков" % (u.name, side, w.name, int((pos < 0).sum())))
        for n, v in rows.raw.items():
            raw.setdefault(n, np.full(len(W), np.nan))[sel] = v[pos]
    _C[key] = (raw, ser, kinds, pnl)
    return _C[key]


def ref_path(side):
    return q("res", "ref-h775-%s.json" % side)


def load_ref(side):
    """ECDF-эталоны и пороги h775 (записаны `counts --uni h775`): ({имя: массив}, {порог: {q: значение}})."""
    d = json.load(open(ref_path(side), encoding="utf-8"))
    return {n: np.array(v, dtype=float) for n, v in d["refs"].items()}, d["thr"]


def values_of(u, side):
    """(d — сырые столбцы + COUP на сделках, ser, kinds, pnl, thr); для o566 эталоны и пороги — из h775."""
    raw, ser, kinds, pnl = trade_values(u, side)
    if u.name == "h775":
        refs = make_refs({k: finite(v) for k, v in raw.items()})
        d = derive(raw, refs)
        thr = quantile_table(d)
        write_json(ref_path(side), dict(refs={n: v.tolist() for n, v in refs.items()}, thr=thr, n_trades=int(len(ser)), code_id=code_id()))
    else:
        refs, thr = load_ref(side)
        d = derive(raw, refs)
    return d, ser, kinds, pnl, thr


# ---- counts (без P&L) --------------------------------------------------------------------------------------------------------

def write_cell_table(path, sym, rel, cols):
    names = list(cols)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(names) + "\n")
        for i in range(len(sym)):
            f.write("%s,%d,%s\n" % (sym[i], int(rel[i]), ",".join(tf.fmt_val(cols[c][i]) for c in names)))
    return len(sym)


def peer_gap(u, wname, side, rows, mon_of):
    """Верхняя оценка (п. 4): число кандидатов окна, у кластера которых в 2 ч до `t` есть событие стороны без записанного пересечения (ok_ev = 0 при locate_fail = 0): `cross_ms` у такого события пусто (NATR ≤ 0 или конец сетки)."""
    import pandas as pd
    d = pd.read_csv(os.path.join(ft.EV_DIR[u.name], wname + ".csv"), usecols=["side", "sym", "sig_ms", "ok_ev", "cross_ms", "locate_fail"])
    d = d[(d["side"] == ft.SIDE_NAME[side]) & (d["ok_ev"] == 0) & (d["locate_fail"] == 0)]
    by = {s: np.sort(g["sig_ms"].to_numpy(dtype=np.int64)) for s, g in d.groupby("sym")}
    clu = {}
    for mon in range(7, 12):
        p = os.path.join(ft.ROOT, "feat", u.name, "clusters-%02d.csv" % mon)
        if os.path.exists(p):
            tab = {}
            for line in open(p, encoding="utf-8").read().splitlines()[1:]:
                s_, c_ = line.split(",")
                tab.setdefault(c_, []).append(s_)
            clu[mon] = {s_: mem for mem in tab.values() for s_ in mem}
    n = 0
    for i in range(len(rows.sym)):
        mon = mon_of[i]
        mem = clu.get(mon, {}).get(rows.sym[i])
        if not mem:
            continue
        t = int(rows.rel[i])
        for m in mem:
            a = by.get(m)
            if a is not None and m != rows.sym[i]:
                j = int(np.searchsorted(a, t - 2 * 3600_000, side="left"))
                if j < len(a) and a[j] < t:
                    n += 1
                    break
    return n


def cmd_counts(a):
    """Таблицы `--ftab`, пороги, покрытие, исключённые по месяцам, дубли, m_new, Jaccard, peer_gap — без P&L. h775 первым (пишет ECDF и пороги), o566 — по ним."""
    u = uni_of(a)
    for side in SIDES:
        d, ser, kinds, pnl, thr = values_of(u, side)
        mon = st.MONTH_OF_DAY[ser.day]
        n_t = len(ser)
        specs = cell_specs()
        res = dict(uni=u.name, side=side, n_trades=int(n_t), thr=thr, coverage={}, dist={}, cells=[], jaccard={}, peer={})
        cols = ["cls", "turn24h", "natr_bp", "age_d", "clu_id", "peer5", "peer15", "peer60", "peerall15"] + ["COUP_" + w for w in W_ALL] + ["%s_%s" % (c, w) for w in W_ALL for c in COMPS]
        for n in cols:
            v = d[n]
            vv = v[np.isfinite(v)]
            res["coverage"][n] = [round(float(np.isfinite(v[mon == k]).mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
            res["dist"][n] = dict(n=int(len(vv)), all=round(float(np.isfinite(v).mean()), 4), q05_25_50_75_95=[round(float(x), 5) for x in np.quantile(vv, [0.05, 0.25, 0.5, 0.75, 0.95])] if len(vv) else None)
        for n in ("peer5", "peer15", "peer60", "peerall15"):
            v = d[n]
            res["peer"][n] = dict(zero=int((v == 0).sum()), one=int((v == 1).sum()), two_plus=int((v >= 2).sum()), nocluster=int(np.isnan(v).sum()))
        masks, seen = {}, {}
        for sp in specs:
            mk = excl_mask(sp, d, thr)
            key = np.packbits(mk).tobytes() + len(mk).to_bytes(8, "little")
            dup = seen.get(key)
            if dup is None:
                seen[key] = sp["cid"]
            masks[sp["cid"]] = mk
            rem = [int(((mon == k) & ~mk).sum()) for k in range(len(st.MONTH_LEN))]
            res["cells"].append(dict(cid=sp["cid"], kind=sp["kind"], fam=sp["fam"], group=sp["group"], dup_of=dup, E=int(mk.sum()), f_all=round(float(mk.mean()), 4),
                                     excl_by_month=[int((mk & (mon == k)).sum()) for k in range(len(st.MONTH_LEN))], remain_by_month=rem, base_by_month=[int((mon == k).sum()) for k in range(len(st.MONTH_LEN))],
                                     admit_floor=bool(all(rem[k] >= st.MIN_TRADES[k] for k in range(len(st.MONTH_LEN)))) and int(mk.sum()) >= st.MIN_EXCL))
        trial = [c for c in res["cells"] if c["kind"] == "T"]
        res["m_trial_nodup"] = sum(1 for c in trial if c["dup_of"] is None)
        res["m_trial_admit_floor"] = sum(1 for c in trial if c["dup_of"] is None and c["admit_floor"])
        for c in trial:
            nbs = [e for e in res["cells"] if e["kind"] == "N" and e["group"] == c["group"]]
            res["jaccard"][c["cid"]] = {e["cid"]: round(st.jaccard(masks[c["cid"]], masks[e["cid"]]), 3) for e in nbs}
        # peer_gap по окнам (кандидаты, не сделки)
        gap = {}
        for w in st.TRADE_WINDOWS:
            rows = rows_of(u, w.name, side)
            mon_of = [ft.month_of(w.t0_ms + int(r)) for r in rows.rel]
            gap[w.name] = dict(candidates=len(rows.sym), peer_gap=peer_gap(u, w.name, side, rows, mon_of))
        res["peer_gap"] = gap
        write_json(q("res", "counts36-%s-%s.json" % (u.name, side)), stamp(res))
        say("== %s %s: сделок базы %d; пробных клеток без дублей %d (с допуском по месяцам %d из %d)" % (u.name, side, n_t, res["m_trial_nodup"], res["m_trial_admit_floor"], len(trial)))
        for n in cols:
            if n.startswith(("cb_", "ce_", "bt_", "cc_")) and not n.endswith(("_15m", "_24h")):
                continue
            say("   %-10s: определён у %5.1f %% сделок, по месяцам %s, квантили 5/25/50/75/95 %s" % (n, 100 * res["dist"][n]["all"], res["coverage"][n], res["dist"][n]["q05_25_50_75_95"]))
        for n, v in res["peer"].items():
            say("   %-9s: 0 → %d, 1 → %d, 2+ → %d, без кластера → %d" % (n, v["zero"], v["one"], v["two_plus"], v["nocluster"]))
        for c in res["cells"]:
            say("   клетка %-18s %s E=%d (%.1f %%) исключено по месяцам %s, останется %s из %s, допуск %s%s" % (
                c["cid"], {"T": "пробная", "N": "сосед", "R": "справ."}[c["kind"]], c["E"], 100 * c["f_all"], c["excl_by_month"], c["remain_by_month"], c["base_by_month"],
                "ok" if c["admit_floor"] else "НЕТ", " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
        for cid, jd in res["jaccard"].items():
            good = sum(1 for v in jd.values() if v <= st.JACCARD_MAX)
            say("   Jaccard %-18s %s; соседей с J ≤ 0,8: %d%s" % (cid, jd, good, "" if good >= 2 else "  ← плато пройти не может (g5)"))
        say("   peer_gap (верхняя оценка): %s" % gap)
        for w in st.TRADE_WINDOWS:
            rows = rows_of(u, w.name, side)
            dr = derive(rows.raw, load_ref(side)[0])
            cols_t = {sp["cid"]: np.where(excl_mask(sp, dr, thr), 0.0, 1.0) for sp in specs}
            n = write_cell_table(q(sub(u, "ftab36"), "%s-%s.csv" % (w.name, side)), rows.sym, rows.rel, cols_t)
            say("ftab36 %s %s-%s: %d строк" % (u.name, w.name, side, n))
    say("counts %s готово — запись в протокол §13.35 п. 13 до чтения P&L" % u.name)


# ---- после PASS критика: ступень 1, клетки, G8, выбор (читают P&L) -----------------------------------------------------------------

def slice_full(ser, kinds, pnl, m):
    """Срез сделок: статистика среза + R:R и убыток стопа (в % маржи при плече 2) — п. 9."""
    r = r34.slice_plus(ser.day[m], ser.val[m])
    if r["n"]:
        x = rr.rr_stats([dict(kind=k, pnl=p) for k, p in zip(kinds[m], pnl[m])])
        r["rr"] = dict(rr=x["rr"], mean_win=x["mean_win"], mean_loss=x["mean_loss"], stops=x["stops"]["n"], stop_loss_pct=x["stops"]["mean_loss_pct"])
    else:
        r["rr"] = None
    return r


def bucket(x, edges):
    """Корзины 0…3 по краям `edges` (≤ q25, ≤ q50, ≤ q75, выше), 4 — нет значения."""
    return r34.quartile_index(x, edges)


def peer_bucket(x, kind):
    """PEER по кластеру: 0 / 1 / 2+ / без кластера (NaN); по всем монетам: 0 / 1–2 / 3–5 / 6+ (NaN нет)."""
    x = np.asarray(x, dtype=float)
    b = np.full(len(x), 4, dtype=np.int64)
    ok = np.isfinite(x)
    if kind == "cluster":
        b[ok & (x == 0)], b[ok & (x == 1)], b[ok & (x >= 2)] = 0, 1, 2
    else:
        b[ok & (x == 0)], b[ok & (x >= 1) & (x <= 2)], b[ok & (x >= 3) & (x <= 5)], b[ok & (x >= 6)] = 0, 1, 2, 3
    return b


PEER_NAMES = {"cluster": ["0", "1", "2+", "—", "без кластера"], "all": ["0", "1–2", "3–5", "6+", "—"]}


def cmd_step1(a):
    """Ступень 1 (п. 9): срезы сделок баз — класс / тип / теги, квартили TOV / AGE / VOL, квартили компонент и COUP по окнам (и 7 сут.), PEER; читает P&L."""
    u = uni_of(a)
    contracts = ft.read_contracts()
    res = {}
    for side in SIDES:
        d, ser, kinds, pnl, thr = values_of(u, side)
        dh = values_of(r5.Uni("h775"), side)[0]
        allm = np.ones(len(ser.val), dtype=bool)
        syms = np.asarray(ser.sym_name, dtype=object)
        r = dict(base=slice_full(ser, kinds, pnl, allm), cls={}, typ={}, tag={}, quart={}, peer={})
        r["cls"] = {nm: slice_full(ser, kinds, pnl, m) for nm, m in (("крипто", d["cls"] == 0), ("TradFi", d["cls"] == 1))}
        types, tags = {}, {}
        for i, s_ in enumerate(syms):
            c = contracts.get(s_)
            if c:
                types.setdefault(c["underlying_type"], []).append(i)
                for t in c["underlying_sub_type"].split("|"):
                    tags.setdefault(t, []).append(i)
        for nm, idx in sorted(types.items()):
            m = np.zeros(len(syms), dtype=bool)
            m[idx] = True
            r["typ"][nm] = slice_full(ser, kinds, pnl, m)
        h_tags = {}
        sh = np.asarray(r5.load_series(r5.Uni("h775"), side, "base").sym_name, dtype=object)
        for i, s_ in enumerate(sh):
            c = contracts.get(s_)
            for t in (c["underlying_sub_type"].split("|") if c else []):
                h_tags[t] = h_tags.get(t, 0) + 1
        for nm, idx in sorted(tags.items()):
            if h_tags.get(nm, 0) >= 20:
                m = np.zeros(len(syms), dtype=bool)
                m[idx] = True
                r["tag"][nm] = slice_full(ser, kinds, pnl, m)
        items = [("TOV", "turn24h"), ("AGE", "age_d"), ("VOL", "natr_bp")]
        for w in W_ALL:
            items += [("%s_%s" % (c, w), "%s_%s" % (c, w)) for c in COMPS] + [("COUP_" + w, "COUP_" + w)]
        for nm, col in items:
            edges = r34.quartile_edges(dh[col])
            bi = bucket(d[col], edges)
            rows = {"все": r["base"]}
            for i, qn in enumerate(("Q1", "Q2", "Q3", "Q4", "нет значения")):
                rows[qn] = slice_full(ser, kinds, pnl, bi == i)
            r["quart"][nm] = dict(edges=edges, rows=rows)
        for nm, col, kind in (("PEER_5м", "peer5", "cluster"), ("PEER_15м", "peer15", "cluster"), ("PEER_60м", "peer60", "cluster"), ("PEER_все_15м", "peerall15", "all")):
            bi = peer_bucket(d[col], kind)
            r["peer"][nm] = {PEER_NAMES[kind][i]: slice_full(ser, kinds, pnl, bi == i) for i in range(5) if (bi == i).any() or kind == "cluster"}
        res[side] = r
        b = r["base"]
        say("== ступень 1 %s %s: сделок %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f, R:R %.2f" % (u.name, side, b["n"], b["sum"], b["mean"], b["sharpe"], b["rr"]["rr"]))
        for nm, qd in r["quart"].items():
            say("   %-10s: %s" % (nm, "; ".join("%s %d сд. %.2f $/сд." % (k, v["n"], v["mean"] if v["n"] else float("nan")) for k, v in qd["rows"].items())))
    write_json(q("res", "step1-%s.json" % u.name), stamp(r33.jsonable(res)))
    say("метка: def %s, код %s; ступень 1 %s готова" % (f5.DEF_TAG, code_id(), u.name))


def cells_dir(u, w, side, cid):
    return os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w, side), cid)


def run_specs(u, side):
    """Клетки, которые гоняет движок: без дублей (список дублей — из counts этой вселенной; нет counts — все)."""
    p = q("res", "counts36-%s-%s.json" % (u.name, side))
    dup = {c["cid"]: c["dup_of"] for c in json.load(open(p, encoding="utf-8"))["cells"]} if os.path.exists(p) else {}
    return [sp for sp in cell_specs() if dup.get(sp["cid"]) is None]


def cell_variants(u, side):
    return [(sp["cid"], "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, sp["cid"], FV_PASS)) for sp in run_specs(u, side)]


def cmd_cells(a):
    u = uni_of(a)
    w = r4.win(a.window)
    for side in SIDES:
        out = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side))
        if os.path.exists(out + "/DONE"):
            continue
        ftab = q(sub(u, "ftab36"), "%s-%s.csv" % (w.name, side))
        r5.tick(u, w, cell_variants(u, side), out, {}, q("log", "cells-%s-%s-%s.log" % (u.name, w.name, side)), ftab=ftab)
        with open(out + "/DONE", "w", encoding="utf-8") as f:
            f.write("%s %s %s" % (f5.DEF_TAG, code_id(), sha256(ftab)))
    say("cells %s %s готово" % (u.name, w.name))


def cmd_cellsall(a):
    for w in st.TRADE_WINDOWS:
        a.window = w.name
        cmd_cells(a)


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` движка = независимому пересчёту по таблице (значение < 0,5) — для всех прогонов клеток (читает только счётчики фильтра)."""
    u = uni_of(a)
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            ftab = q(sub(u, "ftab36"), "%s-%s.csv" % (w.name, side))
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for sp in run_specs(u, side):
                tab = r34.read_table_col(ftab, sp["cid"])
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(cells_dir(u, w.name, side, sp["cid"]), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, sp["cid"])] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, side, sp["cid"], fc, exp_u, exp_m, miss))
    write_json(q("gates", "g8-%s.json" % u.name), dict(res=res, bad=bad))
    say("G8 %s" % u.name, "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


def cmd_gu3(a):
    """G-U3: клетка `CLS` (775 + `t24 = 12`, TradFi исключён таблицей) движком = прогон U3 TK-621 (крипто 539 + `t24 = 12`) по списку сделок (монета, вход); расхождение печатается, в решении клетка `CLS` как есть."""
    u = r5.Uni("h775")
    res = {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            c = st.read_trades_csv(os.path.join(cells_dir(u, w.name, side, "CLS"), ENG[side] + "-trades.csv"))
            e = st.read_trades_csv(os.path.join("/data/p42-tk621/out/crypto_t24", w.name, BASE_NAME[side], ENG[side] + "-trades.csv"))
            kc = {(s_, int(t_)) for s_, t_ in zip(c.sym, c.entry_ms)}
            ke = {(s_, int(t_)) for s_, t_ in zip(e.sym, e.entry_ms)}
            res["%s-%s" % (w.name, side)] = dict(cls=len(kc), u3=len(ke), common=len(kc & ke), only_cls=len(kc - ke), only_u3=len(ke - kc))
    write_json(q("gates", "gu3.json"), res)
    for k, v in res.items():
        say("G-U3 %s: CLS %d сделок, U3 %d, общих %d, только CLS %d, только U3 %d" % (k, v["cls"], v["u3"], v["common"], v["only_cls"], v["only_u3"]))


BASE_NAME = {"L": "L_base", "S": "S_base"}


def check_inputs(u):
    for side in SIDES:
        c = json.load(open(q("res", "counts36-%s-%s.json" % (u.name, side)), encoding="utf-8"))
        if c.get("def") != f5.DEF_TAG:
            raise GuardError("counts36 %s %s: определение %r ≠ %r — вход отвергнут" % (u.name, side, c.get("def"), f5.DEF_TAG))
        for w in st.TRADE_WINDOWS:
            done = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side), "DONE")
            got = open(done, encoding="utf-8").read().split() if os.path.exists(done) else []
            want = sha256(q(sub(u, "ftab36"), "%s-%s.csv" % (w.name, side)))
            if len(got) != 3 or got[0] != f5.DEF_TAG or got[2] != want:
                raise GuardError("клетки %s %s-%s: DONE %s ≠ (%s, …, %s) — вход отвергнут" % (u.name, w.name, side, got, f5.DEF_TAG, want[:12]))


def load_cell_series(u, side, cid):
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d = cells_dir(u, w.name, side, cid)
        tables[w.name] = st.read_trades_csv(os.path.join(d, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, ENG[side] + "-daily.csv"))
    return st.splice(tables, u.coins(), daily=daily)


def build_side(u, side):
    """SideCells вселенной: база + клетки (пробные T и соседи N с рядом; справочная CLS — клетка R: не пробная, без соседей). Дубль — без ряда."""
    d, ser, kinds, pnl, thr = values_of(u, side)
    counts = json.load(open(q("res", "counts36-%s-%s.json" % (u.name, side)), encoding="utf-8"))
    dup = {c["cid"]: c["dup_of"] for c in counts["cells"]}
    cells = []
    for sp in cell_specs():
        mk = excl_mask(sp, d, thr)
        series = None if dup[sp["cid"]] is not None else load_cell_series(u, side, sp["cid"])
        cells.append(s5.Cell(sp["cid"], "-", sp["group"], 0.0, None, sp["kind"] == "T", dup[sp["cid"]], series, mk))
    return s5.SideCells(ser, cells, len(u.coins()))


def joint_holm(ev, ps_old):
    """(g1) по совместному семейству: 29 прежних пробных клеток (p как есть) + новые пробные (семейство = совместное, критик 20.11); рядом — Холм по одним новым и по стороне (контекст)."""
    trial = ev["holm"]["trial"]
    ps_new = ev["holm"]["ps"]
    rej_new = st.holm_reject(ps_new, 0.05) if ps_new else []
    rej_all = st.holm_reject(list(ps_old) + list(ps_new), 0.05)[len(ps_old):]
    rej_side = {}
    for s in SIDES:
        idx = [i for i, (ss, _) in enumerate(trial) if ss == s]
        rr_ = st.holm_reject([ps_new[i] for i in idx], 0.05) if idx else []
        rej_side.update({i: bool(r_) for i, r_ in zip(idx, rr_)})
    passed = []
    for i, (s, cid) in enumerate(trial):
        r = ev["rows"][s][cid]
        ok = bool(r["admitted"] and r["d"] > 0)
        r["g1_new_only"], r["g1_side"] = bool(rej_new[i] and ok), bool(rej_side[i] and ok)
        r["g1"] = bool(rej_all[i] and ok)
        r["pass"] = bool(r["admitted"] and r["g1"] and r.get("g2") and r.get("g3") and r.get("g4") and r.get("g5"))
        if r["pass"]:
            passed.append((s, cid))
    ev["passed"] = passed
    ev["holm"]["m_joint"] = len(ps_old) + len(ps_new)
    ev["holm"]["ps_old"] = list(ps_old)
    return ev


def is_marked(cid):
    """Помечено «виден P&L до записи» (п. 5): семейство TOV."""
    return cid.startswith("TOV_")


def side_outcome(ev, repl, side):
    """Исход стороны (п. 8): «работает» — не помеченная пробная клетка прошла (g1)–(g5) на h775 и повторилась на o566; «кандидат для вперёд» — так прошла только помеченная (TOV_X);
    «не работает» — все допущенные пробные клетки (≥ 1, без CLS) Δ < 0; иначе «не ясно»."""
    ok = [cid for s, cid in ev["passed"] if s == side and repl.get((s, cid), {}).get("repl")]
    works = [c for c in ok if not is_marked(c)]
    cand = [c for c in ok if is_marked(c)]
    adm = [r for r in ev["rows"][side].values() if r["trial"] and r["admitted"]]
    if works:
        return "работает", works
    if cand:
        return "кандидат для вперёд (найдено на этих данных, прирост по построению отбора)", cand
    if adm and all(r["d"] < 0 for r in adm):
        return "не работает", []
    return "не ясно", []


def answer_line(outcomes):
    return "; ".join("%s — %s" % (SIDE_NAME_RU[s], o) for s, (o, _) in outcomes.items())


def group_table(ev, side):
    """По группам признаков (п. 8): число допущенных пробных клеток, прошедших каждое из (g1)–(g5), и Δ ± SE лучшей по Δ клетки группы как область."""
    out = {}
    for fam in GROUPS:
        cids = [sp["cid"] for sp in cell_specs() if sp["kind"] == "T" and sp["fam"] == fam]
        rows = [ev["rows"][side][c] for c in cids if c in ev["rows"][side] and ev["rows"][side][c]["admitted"]]
        best = max(rows, key=lambda r: r["d"]) if rows else None
        out[fam] = dict(trial=len(cids), admitted=len(rows), g1=sum(1 for r in rows if r.get("g1")), g2=sum(1 for r in rows if r.get("g2")), g3=sum(1 for r in rows if r.get("g3")),
                        g4=sum(1 for r in rows if r.get("g4")), g5=sum(1 for r in rows if r.get("g5")), best=None if best is None else dict(cid=best["cid"], d=best["d"], se=best["se"]))
    return out


def cell_slices(sd):
    out = {"база": r34.slice_plus(sd.base.day, sd.base.val)}
    for c in sd.cells:
        c2 = r33.with_series(sd, c)
        if c2 is not None:
            out[c.cid] = r34.slice_plus(c2.series.day, c2.series.val)
    return out


def cmd_select(a):
    """Выбор по п. 6–8 на h775 (совместный Холм m = 29 + m_new, g1–g5; `CLS` справочная), повтор на o566, ответ по сторонам."""
    h, o = r5.Uni("h775"), r5.Uni("o566")
    for u_ in (h, o):
        check_inputs(u_)
    allom = np.arange(st.NDAYS)
    ps_old = []
    for p_ in OLD_SELECT:
        ps_old += json.load(open(p_, encoding="utf-8"))["holm"]["ps"]
    if len(ps_old) != N_OLD_TRIAL:
        raise GuardError("прежних пробных клеток %d ≠ %d (п. 6)" % (len(ps_old), N_OLD_TRIAL))
    sh = {s: build_side(h, s) for s in SIDES}
    ev = joint_holm(s5.evaluate(sh, allom), ps_old)
    so = {s: build_side(o, s) for s in SIDES}
    repl = {}
    for s, cid in ev["passed"]:
        c = r33.with_series(so[s], next(c for c in so[s].cells if c.cid == cid))
        if c is not None:
            repl[(s, cid)] = s5.replicate(so[s], c, allom)
    rows_o = {s: {c.cid: s5.cell_row(so[s], r33.with_series(so[s], c), allom) for c in so[s].cells if r33.with_series(so[s], c) is not None} for s in SIDES}
    outcomes = {s: side_outcome(ev, repl, s) for s in SIDES}
    ans = answer_line(outcomes)
    groups = {s: group_table(ev, s) for s in SIDES}
    sl_h = {s: cell_slices(sh[s]) for s in SIDES}
    sl_o = {s: cell_slices(so[s]) for s in SIDES}
    say("Холм совместный: m = %d (%d прежних + %d новых пробных); p новых %s" % (ev["holm"]["m_joint"], len(ps_old), ev["holm"]["m"], [round(p_, 4) for p_ in ev["holm"]["ps"]]))
    for s in SIDES:
        b = sl_h[s]["база"]
        say("== %s h775: сделок базы %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (s, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, r in ev["rows"][s].items():
            sl = sl_h[s].get(cid) or {}
            say("   %-18s %-7s: Δ %s ± %s $/сделку, z %s, p %s, n_c %d, n_r %d, исключено %d, Σ %s, Sharpe %s, допуск %s, g1 %s (новые %s, сторона %s) g2 %s g3 %s g4 %s g5 %s%s" % (
                cid, "пробная" if r["trial"] else "сосед/справ.", "%.3f" % r["d"] if r["d"] == r["d"] else "—", "%.3f" % r["se"] if r["se"] == r["se"] else "—",
                "%.2f" % r["z"] if r["z"] == r["z"] else "—", "%.4f" % r["p"] if r["p"] == r["p"] else "—", r["n_c"], r["n_r"], r["n_ex"],
                "%.0f" % sl["sum"] if sl else "—", "%.2f" % sl["sharpe"] if sl else "—", r["admitted"], r.get("g1"), r.get("g1_new_only"), r.get("g1_side"),
                r.get("g2"), r.get("g3"), r.get("g4"), r.get("g5"), " ПРОШЛА" if r.get("pass") else ""))
        for fam, g in groups[s].items():
            say("   группа %-26s: допущено %d из %d; g1 %d g2 %d g3 %d g4 %d g5 %d; лучшая по Δ %s" % (fam, g["admitted"], g["trial"], g["g1"], g["g2"], g["g3"], g["g4"], g["g5"], g["best"]))
    for (s, cid), r in repl.items():
        say("   повтор o566 %s %s: Δ %.3f ± %.3f, g2 %s g3 %s g4 %s → %s" % (s, cid, r["d"], r["se"], r.get("g2"), r.get("g3"), r.get("g4"), r["repl"]))
    for s, v in outcomes.items():
        say("ИСХОД %s: %s %s" % (SIDE_NAME_RU[s], *v))
    say("ОТВЕТ: %s" % ans)
    out = dict(answer=ans, outcomes={SIDE_NAME_RU[s]: list(v) for s, v in outcomes.items()}, holm=ev["holm"], passed=ev["passed"], rows=ev["rows"], groups=groups,
               repl={"%s/%s" % k: v for k, v in repl.items()}, rows_o566=rows_o, slices_h775=sl_h, slices_o566=sl_o)
    write_json(q("res", "select36.json"), stamp(r33.jsonable(out)))
    say("метка: def %s, код %s" % (f5.DEF_TAG, code_id()))


COMMANDS = {"counts": cmd_counts, "step1": cmd_step1, "cells": cmd_cells, "cellsall": cmd_cellsall, "g8": cmd_g8, "gu3": cmd_gu3, "select": cmd_select}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("window", nargs="?")
    ap.add_argument("--uni", default="h775", choices=("o566", "h775"))
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
