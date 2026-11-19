#!/usr/bin/env python3
"""П-42 §13.34 (TK-634): импульсные (IMP) и сделочные (FLOW) признаки поверх истории пробоев — композиты, клетки, таблицы `--ftab`, ступень 1, выбор.
Подкоманды (порядок — п. 12 протокола): counts, step1, cellsall, g8, select. P&L читают только step1 / cellsall / g8 / select — после PASS критика; `counts` P&L не читает.
Входы (готовые, не пересчитываются): сырые признаки кандидатов `/data/p42-tk634/feat/<вс>/<окно>-<сторона>.csv` (`tk634_feat.py`), история пробоев `ftab33[-h775]` (TK-633), пороги `cmap-h775.json`
TK-625 / TK-633, база и клетки «только история» TK-625 / TK-633 (эталоны g6).
Композит семейства — среднее процентных рангов (середина ничьих) определённых компонент по ECDF сделок базы h775; клетка — «история ≥ c И композит ≥ fv», в таблице `--ftab` — колонкой 1 / 0."""
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
import tk625_feat as f5  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk625_stats as s5  # noqa: E402
import tk633_run as r33  # noqa: E402
import tk634_feat as ft  # noqa: E402

ROOT = "/data/p42-tk634"
SIDES = st.SIDES
ENG, BASE = r5.ENG, r5.BASE
say, write_json, sha256 = r4.say, r4.write_json, r4.sha256
GuardError = st.GuardError
FAMS = ("IMP", "FLOW")
FAM_NAME = {"IMP": "импульс", "FLOW": "поток"}
SIDE_NAME_RU = {"L": "лонг", "S": "шорт"}
COMPS = {"IMP": ("mv15", "acc_px", "vsurge"), "FLOW": ("tfv300", "big300", "msz_r", "acc_n")}
MIN_AVAIL = {"IMP": 2, "FLOW": 3}                  # п. 3: композит определён при ≥ 2 из 3 / ≥ 3 из 4 компонент
Q_MAIN, Q_NB = 0.25, (0.10, 0.40)                  # п. 6
D_HIST = 14                                        # п. 5: контекст истории — D = 14, m = 1 (соседи m = 0,5 и 2), c — из cmap
FV_PASS = 0.5                                      # движок: `fa=<клетка> fo=lo fv=0.5` — исключает значение 0,0, пропускает 1,0
N_OLD_TRIAL = 21                                   # 9 (TK-625) + 12 (TK-633), п. 7 (g1)
OLD_SELECT = ("/data/p42-tk625/res/select.json", "/data/p42-tk633/res/select33.json")
CMAP_OWN, CMAP_X = "/data/p42-tk625/res/cmap-h775.json", "/data/p42-tk633/res/cmap-h775.json"
NB_Q = 5_000                                       # п. 10: бутстреп контраста взаимодействия
UNITS = [(s, f) for s in SIDES for f in FAMS]
UNIT_NAME = {(s, f): "%s·%s" % (SIDE_NAME_RU[s], FAM_NAME[f]) for s, f in UNITS}
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
    """sha256 содержимого tk634_feat.py, tk634_run.py и tk625_{events,feat,run,stats}.py, tk633_run.py (12 знаков) — метка рядом с каждым числом отчёта."""
    import hashlib
    h = hashlib.sha256()
    for n in ("tk625_events.py", "tk625_feat.py", "tk625_run.py", "tk625_stats.py", "tk633_run.py", "tk634_feat.py", "tk634_run.py"):
        h.update(open(os.path.join(HERE, n), "rb").read())
    return h.hexdigest()[:12]


def stamp(d):
    d["def"], d["code_id"] = f5.DEF_TAG, code_id()
    return d


# ---- признаки и композиты --------------------------------------------------------------------------------------------------------

def finite(x):
    x = np.asarray(x, dtype=float)
    return np.where(np.isfinite(x), x, np.nan)


def components(raw):
    """Компоненты IMP / FLOW из сырых столбцов кандидатов (п. 3); NaN — значение не определено. `acc_px = mv5/5 − (mv60 − mv5)/55`, `vsurge = turn1h·24/turn24h`."""
    mv5, mv60, t1, t24 = (np.asarray(raw[k], dtype=float) for k in ("mv5", "mv60", "turn1h", "turn24h"))
    with np.errstate(invalid="ignore", divide="ignore"):
        acc_px = mv5 / 5.0 - (mv60 - mv5) / 55.0
        vs = np.where(t24 > 0, t1 * 24.0 / np.where(t24 > 0, t24, 1.0), np.nan)
    return {"mv15": finite(raw["mv15"]), "acc_px": finite(acc_px), "vsurge": finite(vs), "tfv300": finite(raw["tfv300"]), "big300": finite(raw["big300"]),
            "msz_r": finite(raw["msz_r"]), "acc_n": finite(raw["acc_n"])}


def ecdf_pct(ref_sorted, x):
    """Процентный ранг значения среди эталонного (отсортированного) массива: (#ref < x + #ref ≤ x) / (2N), середина ничьих; NaN → NaN."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    ref = np.asarray(ref_sorted, dtype=float)
    ok = np.isfinite(x)
    if len(ref) and ok.any():
        out[ok] = (np.searchsorted(ref, x[ok], side="left") + np.searchsorted(ref, x[ok], side="right")) / (2.0 * len(ref))
    return out


def composite(comps, refs, fam):
    """Композит семейства: среднее процентных рангов определённых компонент; определён при ≥ MIN_AVAIL[fam] определённых, иначе NaN (кандидат проходит)."""
    P = np.vstack([ecdf_pct(refs[n], comps[n]) for n in COMPS[fam]])
    avail = np.isfinite(P).sum(axis=0)
    tot = np.nansum(P, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = tot / np.where(avail > 0, avail, 1)
    return np.where(avail >= MIN_AVAIL[fam], m, np.nan)


def make_refs(comps_trades):
    """Эталонные массивы ECDF: отсортированные определённые значения компонент по сделкам базы."""
    return {n: np.sort(v[np.isfinite(v)]) for n, v in comps_trades.items()}


def fv_table(comp_trades_by_fam):
    """Пороги fv: квантиль q композита по сделкам базы (определённые значения), q ∈ {0,10; 0,25; 0,40} (п. 6)."""
    out = {}
    for fam, v in comp_trades_by_fam.items():
        vv = v[np.isfinite(v)]
        out[fam] = {"%.2f" % qq: float(np.quantile(vv, qq)) for qq in (Q_MAIN,) + Q_NB}
    return out


# ---- клетки ------------------------------------------------------------------------------------------------------------------------

def hist_col(ctx, m):
    return ("x" if ctx == "cross" else "") + f5.col_name(m, D_HIST)


def cmaps():
    if "cm" not in _C:
        _C["cm"] = (json.load(open(CMAP_OWN, encoding="utf-8")), json.load(open(CMAP_X, encoding="utf-8")))
    return _C["cm"]


def hist_c(side, ctx, m):
    own, cross = cmaps()
    return int(own[side]["m%s_d%d_q15" % (m, D_HIST)]) if ctx == "own" else int(cross[side]["X_m%s_d%d_q15" % (m, D_HIST)])


def cell_specs(side):
    """Клетки стороны (п. 6): 4 пробные T (семейство × контекст), по 4 соседа N (q 0,10 / 0,40 при той же истории; история m 0,5 / 2 при q 0,25), 2 справочные R (семейство без истории). 22 шт."""
    specs, g = [], 0
    for fam in FAMS:
        for ctx in ("own", "cross"):
            g += 1
            base = "%s_%s" % (fam, ctx)
            specs.append(dict(cid=base, kind="T", fam=fam, ctx=ctx, q=Q_MAIN, m="10", group=g))
            for qq in Q_NB:
                specs.append(dict(cid="%s_q%02d" % (base, int(round(qq * 100))), kind="N", fam=fam, ctx=ctx, q=qq, m="10", group=g))
            for m in ("05", "20"):
                specs.append(dict(cid="%s_m%s" % (base, m), kind="N", fam=fam, ctx=ctx, q=Q_MAIN, m=m, group=g))
    for i, fam in enumerate(FAMS):
        specs.append(dict(cid="%s_only" % fam, kind="R", fam=fam, ctx=None, q=Q_MAIN, m=None, group=10 + i))
    return specs


def spec_c(side, sp):
    return None if sp["ctx"] is None else hist_c(side, sp["ctx"], sp["m"])


def excl_mask(side, sp, hist, comp_rk, fv):
    """Исключённые строки: (история определена и < c) ИЛИ (композит определён и < fv); пусто проходит."""
    ex = np.zeros(len(comp_rk[sp["fam"]]), dtype=bool)
    if sp["ctx"] is not None:
        h = np.asarray(hist[hist_col(sp["ctx"], sp["m"])], dtype=float)
        ex |= np.isfinite(h) & (h < hist_c(side, sp["ctx"], sp["m"]))
    c = np.asarray(comp_rk[sp["fam"]], dtype=float)
    ex |= np.isfinite(c) & (c < fv[sp["fam"]]["%.2f" % sp["q"]])
    return ex


# ---- чтение таблиц кандидатов и сделок -----------------------------------------------------------------------------------------

class Rows:
    """Строки дампа кандидатов базы (окно, сторона): сырые признаки + история TK-633; ключ (sym, sig_ms от T0) один и тот же."""

    def __init__(self, u, wname, side):
        s1, m1, raw = rfx.read_ftab(os.path.join(ft.ROOT, "feat", u.name, "%s-%s.csv" % (wname, side)))
        s2, m2, hist = rfx.read_ftab(os.path.join(r33.ROOT, sub(u, "ftab33"), "%s-%s.csv" % (wname, side)))
        if s1 != s2 or not np.array_equal(m1, m2):
            raise GuardError("%s %s-%s: ключи таблицы признаков и истории TK-633 различаются (%d vs %d строк)" % (u.name, wname, side, len(s1), len(s2)))
        self.sym, self.rel, self.raw, self.hist = s1, m1, raw, hist
        self.index = {(s, int(r)): i for i, (s, r) in enumerate(zip(s1, m1))}
        self.comps = components(raw)


def rows_of(u, wname, side):
    key = ("rows", u.name, wname, side)
    if key not in _C:
        _C[key] = Rows(u, wname, side)
    return _C[key]


def trade_rows(u, side):
    """Ключи зачётных сделок базы в порядке `r5.load_series` (окно → строки файла): окно, монета, мс от T0."""
    key = ("trows", u.name, side)
    if key in _C:
        return _C[key]
    W, S, R = [], [], []
    for w in st.TRADE_WINDOWS:
        t = st.read_trades_csv(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
        d = st.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
            W.append(w.name)
            S.append(t.sym[i])
            R.append(int(t.sig_ms[i]))
    out = (np.array(W, dtype=object), np.array(S, dtype=object), np.array(R, dtype=np.int64))
    _C[key] = out
    return out


def trade_values(u, side):
    """Значения на сделках базы: ({компонента: массив}, {столбец истории: массив}, ряд сделок базы). Охрана порядка: монета и время сигнала равны ряду `load_series`."""
    key = ("tvals", u.name, side)
    if key in _C:
        return _C[key]
    W, S, R = trade_rows(u, side)
    ser = r5.load_series(u, side, "base")
    t0 = {w.name: w.t0_ms for w in st.TRADE_WINDOWS}
    if list(ser.sym_name) != list(S) or not np.array_equal(ser.key_ms, R + np.array([t0[w] for w in W], dtype=np.int64)):
        raise GuardError("%s %s: порядок сделок базы не совпал с рядом load_series" % (u.name, side))
    comps = {n: np.full(len(W), np.nan) for names in COMPS.values() for n in names}
    hist = {}
    for w in st.TRADE_WINDOWS:
        rows = rows_of(u, w.name, side)
        sel = np.nonzero(W == w.name)[0]
        pos = np.array([rows.index.get((S[i], int(R[i])), -1) for i in sel], dtype=np.int64)
        if (pos < 0).any():
            raise GuardError("%s %s %s: %d сделок базы без строки таблицы кандидатов" % (u.name, side, w.name, int((pos < 0).sum())))
        for n, v in rows.comps.items():
            comps[n][sel] = v[pos]
        for n, v in rows.hist.items():
            hist.setdefault(n, np.full(len(W), np.nan))[sel] = v[pos]
    _C[key] = (comps, hist, ser)
    return _C[key]


def ref_path(side):
    return q("res", "ref-h775-%s.json" % side)


def load_ref(side):
    """ECDF-эталоны и пороги fv h775 (записаны `counts --uni h775`): ({компонента: массив}, {семейство: {q: fv}})."""
    d = json.load(open(ref_path(side), encoding="utf-8"))
    return {n: np.array(v, dtype=float) for n, v in d["refs"].items()}, d["fv"]


def composites(comps, refs):
    return {fam: composite(comps, refs, fam) for fam in FAMS}


# ---- counts (без P&L) --------------------------------------------------------------------------------------------------------

def write_cell_table(path, sym, rel, cols, extra):
    names = list(cols) + list(extra)
    n = len(sym)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(names) + "\n")
        for i in range(n):
            f.write("%s,%d,%s\n" % (sym[i], int(rel[i]), ",".join(tf.fmt_val((cols[c] if c in cols else extra[c])[i]) for c in names)))
    return n


def cmd_counts(a):
    """Таблицы `--ftab` (колонка на клетку + композиты), пороги, покрытие, исключённые по месяцам, дубли, Jaccard, квадранты — без P&L. h775 первым (пишет ECDF и fv), o566 — по ним."""
    u = uni_of(a)
    for side in SIDES:
        comps_t, hist_t, ser = trade_values(u, side)
        if u.name == "h775":
            refs = make_refs(comps_t)
            comp_t = composites(comps_t, refs)
            fv = fv_table(comp_t)
            write_json(ref_path(side), dict(refs={n: v.tolist() for n, v in refs.items()}, fv=fv, n_trades=int(len(ser)), code_id=code_id()))
        else:
            refs, fv = load_ref(side)
            comp_t = composites(comps_t, refs)
        mon = st.MONTH_OF_DAY[ser.day]
        n_t = len(ser)
        specs = cell_specs(side)
        res = dict(uni=u.name, side=side, n_trades=int(n_t), fv=fv, coverage={}, dist={}, cells=[], quad={}, jaccard={})
        for n, v in comps_t.items():
            vv = v[np.isfinite(v)]
            res["dist"][n] = dict(n=int(len(vv)), q05_25_50_75_95=[round(float(x), 5) for x in np.quantile(vv, [0.05, 0.25, 0.5, 0.75, 0.95])] if len(vv) else None)
            res["coverage"][n] = [round(float(np.isfinite(v[mon == k]).mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
        for fam in FAMS:
            res["coverage"]["comp_" + fam] = [round(float(np.isfinite(comp_t[fam][mon == k]).mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
            res["coverage"]["comp_%s_all" % fam] = round(float(np.isfinite(comp_t[fam]).mean()), 4)
            res["dist"]["comp_" + fam] = dict(excl_share_q25=round(float((comp_t[fam][np.isfinite(comp_t[fam])] < fv[fam]["0.25"]).mean()), 4))
        for c_ in ("own", "cross"):
            for m in ("10", "05", "20"):
                col = hist_col(c_, m)
                h = hist_t[col]
                res["coverage"][col] = round(float(np.isfinite(h).mean()), 4)
        # охрана: основной порог истории = 1 (п. 5)
        for c_ in ("own", "cross"):
            if hist_c(side, c_, "10") != 1:
                raise GuardError("%s: порог истории %s m=10 = %d ≠ 1 (п. 5)" % (side, c_, hist_c(side, c_, "10")))
        masks, seen = {}, {}
        for sp in specs:
            mk = excl_mask(side, sp, hist_t, comp_t, fv)
            key = np.packbits(mk).tobytes() + len(mk).to_bytes(8, "little")
            dup = seen.get(key)
            if dup is None:
                seen[key] = sp["cid"]
            masks[sp["cid"]] = mk
            rem = [int(((mon == k) & ~mk).sum()) for k in range(len(st.MONTH_LEN))]
            res["cells"].append(dict(cid=sp["cid"], kind=sp["kind"], fam=sp["fam"], ctx=sp["ctx"], q=sp["q"], m=sp["m"], c=spec_c(side, sp), group=sp["group"], dup_of=dup, E=int(mk.sum()),
                                     f_all=round(float(mk.mean()), 4), excl_by_month=[int((mk & (mon == k)).sum()) for k in range(len(st.MONTH_LEN))], remain_by_month=rem,
                                     base_by_month=[int((mon == k).sum()) for k in range(len(st.MONTH_LEN))],
                                     admit_floor=bool(all(rem[k] >= st.MIN_TRADES[k] for k in range(len(st.MONTH_LEN)))) and int(mk.sum()) >= st.MIN_EXCL))
        trial = [c for c in res["cells"] if c["kind"] == "T"]
        res["m_trial_nodup"] = sum(1 for c in trial if c["dup_of"] is None)
        for c in trial:
            nbs = [d for d in res["cells"] if d["kind"] == "N" and d["group"] == c["group"]]
            res["jaccard"][c["cid"]] = {d["cid"]: round(st.jaccard(masks[c["cid"]], masks[d["cid"]]), 3) for d in nbs}
            hist_pass = ~(np.isfinite(hist_t[hist_col(c["ctx"], "10")]) & (hist_t[hist_col(c["ctx"], "10")] < hist_c(side, c["ctx"], "10")))
            fam_pass = ~(np.isfinite(comp_t[c["fam"]]) & (comp_t[c["fam"]] < fv[c["fam"]]["0.25"]))
            res["quad"][c["cid"]] = dict(hist_pass_fam_pass=int((hist_pass & fam_pass).sum()), hist_pass_fam_fail=int((hist_pass & ~fam_pass).sum()),
                                         hist_fail_fam_pass=int((~hist_pass & fam_pass).sum()), hist_fail_fam_fail=int((~hist_pass & ~fam_pass).sum()))
        write_json(q("res", "counts34-%s-%s.json" % (u.name, side)), stamp(res))
        say("== %s %s: сделок базы %d; пробных клеток без дублей %d" % (u.name, side, n_t, res["m_trial_nodup"]))
        for fam in FAMS:
            say("   композит %s: определён у %.1f %% сделок, по месяцам %s; исключено при q=0,25 (порог fv %.4f) %.1f %%" % (
                fam, 100 * res["coverage"]["comp_%s_all" % fam], res["coverage"]["comp_" + fam], fv[fam]["0.25"], 100 * res["dist"]["comp_" + fam]["excl_share_q25"]))
        for n in sum(COMPS.values(), ()):
            say("   компонента %-7s: определена у %5d сделок, квантили 5/25/50/75/95 %s, по месяцам %s" % (n, res["dist"][n]["n"], res["dist"][n]["q05_25_50_75_95"], res["coverage"][n]))
        for c in res["cells"]:
            say("   клетка %-14s %s c=%s E=%d (%.1f %%) исключено по месяцам %s, останется %s из %s, порог допуска %s%s" % (
                c["cid"], {"T": "пробная", "N": "сосед", "R": "справ."}[c["kind"]], c["c"], c["E"], 100 * c["f_all"], c["excl_by_month"], c["remain_by_month"], c["base_by_month"],
                "ok" if c["admit_floor"] else "НЕТ", " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
        for cid, jd in res["jaccard"].items():
            good = sum(1 for v in jd.values() if v <= st.JACCARD_MAX)
            say("   Jaccard %-12s %s; соседей с J ≤ 0,8: %d%s" % (cid, jd, good, "" if good >= 2 else "  ← плато пройти не может (g5)"))
        for cid, qd in res["quad"].items():
            say("   квадранты %-12s %s" % (cid, qd))
        # таблицы --ftab на строки дампа окон
        for w in st.TRADE_WINDOWS:
            rows = rows_of(u, w.name, side)
            comp_r = composites(rows.comps, refs)
            cols = {sp["cid"]: np.where(excl_mask(side, sp, rows.hist, comp_r, fv), 0.0, 1.0) for sp in specs}
            n = write_cell_table(q(sub(u, "ftab34"), "%s-%s.csv" % (w.name, side)), rows.sym, rows.rel, cols, {"imp_rk": comp_r["IMP"], "flow_rk": comp_r["FLOW"]})
            say("ftab34 %s %s-%s: %d строк" % (u.name, w.name, side, n))
    say("counts %s готово — запись в протокол §13.34 п. 13 до чтения P&L" % u.name)


# ---- после PASS критика: ступень 1, клетки, G8, выбор (читают P&L) -----------------------------------------------------------------

def month_sharpes(day, val):
    """Годовой Sharpe суточного ряда Σ $ внутри каждого месяца (дни месяца, нули в сутки без сделок), × √365."""
    Sd = np.bincount(np.asarray(day, dtype=np.int64), weights=np.asarray(val, dtype=float), minlength=st.NDAYS)
    out = []
    for k in range(len(st.MONTH_LEN)):
        s = Sd[st.MONTH_OF_DAY == k]
        sd = float(s.std(ddof=1)) if len(s) > 1 else 0.0
        out.append(float(s.mean() / sd * np.sqrt(s5.YEAR_DAYS)) if sd > 0 else float("nan"))
    return out


def slice_plus(day, val):
    r = s5.slice_stats(day, val)
    r["sharpe_months"] = month_sharpes(day, val)
    return r


def quartile_edges(vals_h):
    vv = vals_h[np.isfinite(vals_h)]
    return [float(x) for x in np.quantile(vv, [0.25, 0.5, 0.75])]


def quartile_index(x, edges):
    """0…3 — квартиль по краям h775 (≤ q25 → 0, ≤ q50 → 1, ≤ q75 → 2, иначе 3); 4 — нет значения."""
    x = np.asarray(x, dtype=float)
    b = np.full(len(x), 4, dtype=np.int64)
    ok = np.isfinite(x)
    b[ok] = np.searchsorted(np.asarray(edges), x[ok], side="left")
    return b


def interaction(day, val, hist_pass, fam_pass, nboot=NB_Q):
    """Контраст взаимодействия (μ_ИС − μ_И¬С) − (μ_¬ИС − μ_¬И¬С) по четырём квадрантам, 90 % интервал: бутстреп по суткам (блок 4)."""
    quads = [(hist_pass & fam_pass), (hist_pass & ~fam_pass), (~hist_pass & fam_pass), (~hist_pass & ~fam_pass)]
    Sd = [np.bincount(day[m], weights=val[m], minlength=st.NDAYS) for m in quads]
    Nd = [np.bincount(day[m], minlength=st.NDAYS).astype(float) for m in quads]
    if any(n.sum() == 0 for n in Nd):
        return dict(contrast=None, lo90=None, hi90=None, means=[None] * 4, n=[int(n.sum()) for n in Nd])
    means = [float(s.sum() / n.sum()) for s, n in zip(Sd, Nd)]
    c0 = (means[0] - means[1]) - (means[2] - means[3])
    seed = st.seed_of(Sd[0], Nd[0], Sd[3], Nd[3])
    idx = st.block_idx(st.NDAYS, np.random.default_rng(seed), nboot)
    bS = [s[idx].sum(axis=1) for s in Sd]
    bN = [n[idx].sum(axis=1) for n in Nd]
    ok = np.all([n > 0 for n in bN], axis=0)
    out = dict(contrast=float(c0), means=means, n=[int(n.sum()) for n in Nd], lo90=None, hi90=None)
    if ok.mean() >= 0.99:
        bm = [s[ok] / n[ok] for s, n in zip(bS, bN)]
        bc = (bm[0] - bm[1]) - (bm[2] - bm[3])
        out["lo90"], out["hi90"] = [float(x) for x in np.percentile(bc, [5, 95])]
    return out


def cmd_step1(a):
    """Ступень 1 (п. 10): квартили композитов и компонент, квадранты 2 × 2 истории × семейства с контрастом взаимодействия — на сделках баз (читает P&L)."""
    u = uni_of(a)
    res = {}
    for side in SIDES:
        comps_t, hist_t, ser = trade_values(u, side)
        _refs_h, fv = load_ref(side)
        refs_h = {n: np.array(v, dtype=float) for n, v in json.load(open(ref_path(side), encoding="utf-8"))["refs"].items()}
        comp_t = composites(comps_t, refs_h)
        comps_h = trade_values(r5.Uni("h775"), side)[0]
        comp_h = composites(comps_h, refs_h)
        r = dict(base=slice_plus(ser.day, ser.val), quart={}, quad={})
        for name, v, vh in [("comp_" + f, comp_t[f], comp_h[f]) for f in FAMS] + [(n, comps_t[n], comps_h[n]) for n in sum(COMPS.values(), ())]:
            edges = quartile_edges(vh)
            bi = quartile_index(v, edges)
            rows = {"все": r["base"]}
            for i, nm in enumerate(("Q1", "Q2", "Q3", "Q4", "нет значения")):
                m_ = bi == i
                rows[nm] = slice_plus(ser.day[m_], ser.val[m_])
            r["quart"][name] = dict(edges=edges, rows=rows)
        for sp in [s_ for s_ in cell_specs(side) if s_["kind"] == "T"]:
            h = hist_t[hist_col(sp["ctx"], "10")]
            hp = ~(np.isfinite(h) & (h < hist_c(side, sp["ctx"], "10")))
            fp = ~(np.isfinite(comp_t[sp["fam"]]) & (comp_t[sp["fam"]] < fv[sp["fam"]]["0.25"]))
            quads = {nm: slice_plus(ser.day[m_], ser.val[m_]) for nm, m_ in (("история да · семейство да", hp & fp), ("история да · семейство нет", hp & ~fp),
                                                                              ("история нет · семейство да", ~hp & fp), ("история нет · семейство нет", ~hp & ~fp))}
            r["quad"][sp["cid"]] = dict(rows=quads, did=interaction(ser.day, ser.val, hp, fp))
        res[side] = r
        b = r["base"]
        say("== ступень 1 %s %s: сделок %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (u.name, side, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, qd in r["quad"].items():
            say("   %-12s: %s; контраст %s [%s; %s]" % (cid, "; ".join("%s: %d сд., %.2f $/сд." % (k, v["n"], v["mean"] if v["n"] else float("nan")) for k, v in qd["rows"].items()),
                                                         qd["did"]["contrast"], qd["did"]["lo90"], qd["did"]["hi90"]))
    write_json(q("res", "step1-%s.json" % u.name), stamp(r33.jsonable(res)))
    say("метка: def %s, код %s; ступень 1 %s готова" % (f5.DEF_TAG, code_id(), u.name))


def cells_dir(u, w, side, cid):
    return os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w, side), cid)


def cell_variants(u, side):
    return [(sp["cid"], "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, sp["cid"], FV_PASS)) for sp in cell_specs(side)]


def cmd_cells(a):
    u = uni_of(a)
    w = r4.win(a.window)
    for side in SIDES:
        out = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side))
        if os.path.exists(out + "/DONE"):
            continue
        ftab = q(sub(u, "ftab34"), "%s-%s.csv" % (w.name, side))
        r5.tick(u, w, cell_variants(u, side), out, {}, q("log", "cells-%s-%s-%s.log" % (u.name, w.name, side)), ftab=ftab)
        with open(out + "/DONE", "w", encoding="utf-8") as f:
            f.write("%s %s %s" % (f5.DEF_TAG, code_id(), sha256(ftab)))
    say("cells %s %s готово" % (u.name, w.name))


def cmd_cellsall(a):
    for w in st.TRADE_WINDOWS:
        a.window = w.name
        cmd_cells(a)


def read_table_col(path, col):
    """{(монета, мс от T0): значение столбца `col`} таблицы `--ftab` (пусто → NaN)."""
    import csv
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["sym"], int(r["sig_ms"]))] = float(r[col]) if r[col] != "" else float("nan")
    return out


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` движка = независимому пересчёту по таблице (значение < 0,5) — для всех прогонов клеток (читает только счётчики фильтра)."""
    u = uni_of(a)
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            ftab = q(sub(u, "ftab34"), "%s-%s.csv" % (w.name, side))
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for sp in cell_specs(side):
                tab = read_table_col(ftab, sp["cid"])
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


def check_inputs(u):
    for side in SIDES:
        c = json.load(open(q("res", "counts34-%s-%s.json" % (u.name, side)), encoding="utf-8"))
        if c.get("def") != f5.DEF_TAG:
            raise GuardError("counts34 %s %s: определение %r ≠ %r — вход отвергнут" % (u.name, side, c.get("def"), f5.DEF_TAG))
        for w in st.TRADE_WINDOWS:
            done = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side), "DONE")
            got = open(done, encoding="utf-8").read().split() if os.path.exists(done) else []
            want = sha256(q(sub(u, "ftab34"), "%s-%s.csv" % (w.name, side)))
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
    """SideCells вселенной: база + клетки (пробные и соседи с рядом; справочные «только семейство» — тоже клетки с D = 10 + номер, не пробные и не соседи). Дубль — без ряда."""
    comps_t, hist_t, ser = trade_values(u, side)
    refs, fv = load_ref(side)
    comp_t = composites(comps_t, refs)
    counts = json.load(open(q("res", "counts34-%s-%s.json" % (u.name, side)), encoding="utf-8"))
    dup = {c["cid"]: c["dup_of"] for c in counts["cells"]}
    cells = []
    for sp in cell_specs(side):
        mk = excl_mask(side, sp, hist_t, comp_t, fv)
        series = None if dup[sp["cid"]] is not None else load_cell_series(u, side, sp["cid"])
        cells.append(s5.Cell(sp["cid"], sp["m"] or "-", sp["group"], 0.0, spec_c(side, sp), sp["kind"] == "T", dup[sp["cid"]], series, mk))
    return s5.SideCells(ser, cells, len(u.coins()))


def ref_history_series(u, side, ctx):
    """Эталон «только история» (g6): TK-625 `m10_d14_q15` (своя), TK-633 `X_m10_d14_q15` (чужая) — готовые прогоны, не пересчитываются."""
    return r5.load_series(u, side, "cell", "m10_d14_q15") if ctx == "own" else r33.load_series(u, side, "cell", "X_m10_d14_q15")


def g6_rows(u, sd, side):
    """(g6) для каждой пробной клетки: Δ (клетка − только история) и Δ (клетка − только семейство) по суткам, точечно > 0 обе."""
    out = {}
    byc = {c.cid: c for c in sd.cells}
    for sp in cell_specs(side):
        if sp["kind"] != "T":
            continue
        c = byc[sp["cid"]]
        if c.series is None:
            continue
        Sc, Nc = st.day_sums(c.series)
        res = {}
        for nm, ref in (("history", ref_history_series(u, side, sp["ctx"])), ("family", byc["%s_only" % sp["fam"]].series)):
            Sr, Nr = st.day_sums(ref)
            ds = st.delta_stat(Sc, Nc, Sr, Nr)
            res[nm] = dict(d=ds.d, se=ds.se, n_r=int(Nr.sum()))
        res["ok"] = bool(res["history"]["d"] > 0 and res["family"]["d"] > 0)
        out[sp["cid"]] = res
    return out


def joint_holm(ev, ps_old, g6):
    """(g1) по совместному семейству: 21 прежняя клетка (p как есть) + новые пробные; (g6) и итоговое `pass` (g1–g6)."""
    trial = ev["holm"]["trial"]
    ps_new = ev["holm"]["ps"]
    rej_new = st.holm_reject(ps_new, 0.05) if ps_new else []
    rej_all = st.holm_reject(list(ps_old) + list(ps_new), 0.05)[len(ps_old):]
    passed = []
    for i, (s, cid) in enumerate(trial):
        r = ev["rows"][s][cid]
        r["g1_new_only"] = bool(rej_new[i] and r["admitted"] and r["d"] > 0)
        r["g1"] = bool(rej_all[i] and r["admitted"] and r["d"] > 0)
        gg = g6[s].get(cid, {})
        r["g6"] = bool(gg.get("ok"))
        r["g6_note"] = "Δ к «только история» %s ± %s, к «только семейство» %s ± %s" % tuple(
            "%.3f" % gg[k][f] if gg else "—" for k in ("history", "family") for f in ("d", "se")) if gg else "—"
        r["pass"] = bool(r["admitted"] and r["g1"] and r.get("g2") and r.get("g3") and r.get("g4") and r.get("g5") and r["g6"])
        if r["pass"]:
            passed.append((s, cid))
    ev["passed"] = passed
    ev["holm"]["m_joint"] = len(ps_old) + len(ps_new)
    ev["holm"]["ps_old"] = list(ps_old)
    return ev


def unit_outcome(ev, repl, side, fam):
    """Исход единицы (п. 8): «работает» — клетка единицы прошла (g1)–(g6) и повторилась на o566; «не работает» — все допущенные пробные клетки Δ < 0; иначе «не ясно»."""
    cids = ["%s_own" % fam, "%s_cross" % fam]
    works = [cid for cid in cids if (side, cid) in ev["passed"] and repl.get((side, cid), {}).get("repl")]
    adm = [ev["rows"][side][cid] for cid in cids if cid in ev["rows"][side] and ev["rows"][side][cid]["trial"] and ev["rows"][side][cid]["admitted"]]
    if works:
        return "работает", works
    if adm and all(r["d"] < 0 for r in adm):
        return "не работает", []
    return "не ясно", []


def answer_line(outcomes):
    names = [UNIT_NAME[k] for k, (o, _) in outcomes.items() if o == "работает"]
    if names:
        return "работает (%s)" % ", ".join(names)
    if all(o == "не работает" for o, _ in outcomes.values()):
        return "не работает"
    return "не ясно"


def with_series(side, c):
    return r33.with_series(side, c)


def cell_slices(sd):
    out = {"база": slice_plus(sd.base.day, sd.base.val)}
    for c in sd.cells:
        c2 = with_series(sd, c)
        if c2 is not None:
            out[c.cid] = slice_plus(c2.series.day, c2.series.val)
    return out


def cmd_select(a):
    """Выбор по п. 7 на h775 (совместный Холм m = 21 + 8, g1–g6), повтор на o566, ответ по п. 8."""
    h, o = r5.Uni("h775"), r5.Uni("o566")
    for u_ in (h, o):
        check_inputs(u_)
    allom = np.arange(st.NDAYS)
    ps_old = []
    for p_ in OLD_SELECT:
        ps_old += json.load(open(p_, encoding="utf-8"))["holm"]["ps"]
    if len(ps_old) != N_OLD_TRIAL:
        raise GuardError("прежних пробных клеток %d ≠ %d (п. 7)" % (len(ps_old), N_OLD_TRIAL))
    sh = {s: build_side(h, s) for s in SIDES}
    ev0 = s5.evaluate(sh, allom)
    g6 = {s: g6_rows(h, sh[s], s) for s in SIDES}
    ev = joint_holm(ev0, ps_old, g6)
    so = {s: build_side(o, s) for s in SIDES}
    repl = {}
    for s, cid in ev["passed"]:
        c = with_series(so[s], next(c for c in so[s].cells if c.cid == cid))
        if c is not None:
            repl[(s, cid)] = s5.replicate(so[s], c, allom)
    rows_o = {s: {c.cid: s5.cell_row(so[s], with_series(so[s], c), allom) for c in so[s].cells if with_series(so[s], c) is not None} for s in SIDES}
    outcomes = {k: unit_outcome(ev, repl, *k) for k in UNITS}
    ans = answer_line(outcomes)
    sl_h = {s: cell_slices(sh[s]) for s in SIDES}
    sl_o = {s: cell_slices(so[s]) for s in SIDES}
    say("Холм совместный: m = %d (%d прежних + %d новых пробных); p новых %s" % (ev["holm"]["m_joint"], len(ps_old), ev["holm"]["m"], [round(p_, 4) for p_ in ev["holm"]["ps"]]))
    for s in SIDES:
        b = sl_h[s]["база"]
        say("== %s h775: сделок базы %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (s, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, r in ev["rows"][s].items():
            sl = sl_h[s].get(cid) or {}
            say("   %-14s %-7s: Δ %s ± %s $/сделку, z %s, p %s, n_c %d, n_r %d, исключено %d, Σ %s, Sharpe %s, допуск %s, g1 %s (новые %s) g2 %s g3 %s g4 %s g5 %s g6 %s%s%s" % (
                cid, "пробная" if r["trial"] else "сосед/справ.", "%.3f" % r["d"] if r["d"] == r["d"] else "—", "%.3f" % r["se"] if r["se"] == r["se"] else "—",
                "%.2f" % r["z"] if r["z"] == r["z"] else "—", "%.4f" % r["p"] if r["p"] == r["p"] else "—", r["n_c"], r["n_r"], r["n_ex"],
                "%.0f" % sl["sum"] if sl else "—", "%.2f" % sl["sharpe"] if sl else "—", r["admitted"], r.get("g1"), r.get("g1_new_only"), r.get("g2"), r.get("g3"), r.get("g4"),
                r.get("g5"), r.get("g6"), " ПРОШЛА" if r.get("pass") else "", ("; " + r["g6_note"]) if r.get("g6_note") else ""))
    for (s, cid), r in repl.items():
        say("   повтор o566 %s %s: Δ %.3f ± %.3f, g2 %s g3 %s g4 %s → %s" % (s, cid, r["d"], r["se"], r.get("g2"), r.get("g3"), r.get("g4"), r["repl"]))
    for k, v in outcomes.items():
        say("ИСХОД %s: %s %s" % (UNIT_NAME[k], *v))
    say("ОТВЕТ: %s" % ans)
    out = dict(answer=ans, outcomes={UNIT_NAME[k]: list(v) for k, v in outcomes.items()}, holm=ev["holm"], passed=ev["passed"], rows=ev["rows"], g6=g6,
               repl={"%s/%s" % k: v for k, v in repl.items()}, rows_o566=rows_o, slices_h775=sl_h, slices_o566=sl_o)
    write_json(q("res", "select34.json"), stamp(r33.jsonable(out)))
    say("метка: def %s, код %s" % (f5.DEF_TAG, code_id()))


COMMANDS = {"counts": cmd_counts, "step1": cmd_step1, "cells": cmd_cells, "cellsall": cmd_cellsall, "g8": cmd_g8, "select": cmd_select}


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
