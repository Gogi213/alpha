#!/usr/bin/env python3
"""TK-493, П-39 §4, §9, §10: таблицы ПОИСКА по сделкам и знаменателям, записанным conv_explore (work/arrays/<tag>/<id>.npz + pairs.json). Без статусов и без Холма.

Метрика y_d (P-38 §7.4, cell_status.build_series): (Σ net закрытых сделок суток d + MTM открытых на конце в последние сутки) / число активных пар-суток; сутки без активных пар в ряд не входят.
Интервалы - те же функции (portfolio.block_t_interval, студентизованный блочный бутстреп B = 3000, seed 37), 90 %. Половины: H1 сутки 2466..2478, H2 2479..2499 (по суткам закрытия f_out).
Таблицы пишутся под <out>/tables с баннером «ПОИСК — не подтверждение»; читать их как описание 35 суток, выбранное пост hoc.
  python explore_stats.py tables --out ~/xdl9/c2x [--tags main,gate,eq,s2]      основные таблицы (§4), срезы, парные разности, диагностики §10
  python explore_stats.py null   --out ~/xdl9/c2x [--tags ...]                   нуль на направленный навык и устойчивость отбора (§9)
"""
import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_lib as L            # noqa: E402
import conv_explore as EX       # noqa: E402
import conv_agg as AG           # noqa: E402

P, CS = AG.P, AG.CS
COLS = EX.COLS
TF = {c: i for i, c in enumerate(COLS)}
D0, D_LAST, P0 = EX.D0, EX.D_LAST, EX.P0_DEFAULT
H1_LAST = EX.H1_LAST
CODES = L.CODES
C_TAKER, C_LIMIT, C_MAKER = 12.6, 8.82, 5.04
BANNER_NOTE = "ПОИСК — выбрано по 35 суткам, пост hoc"


# ------------------------------------------------------------------ загрузка
class VD:
    """Один вариант: сделки (rows), номера пар (pidx), активные пары-сутки (act), метаданные пар."""

    def __init__(self, v, rows, pidx, act, pairs, src=None):
        self.v, self.rows, self.pidx, self.act, self.pairs, self.src = v, rows, pidx, act, pairs, src
        self.id = v["id"]
        self.net = rows[:, TF["net"]] if len(rows) else np.zeros(0)

    def col(self, name):
        return self.rows[:, TF[name]]

    def pair_exch(self):
        a = np.array([self.pairs[i][2] for i in self.pidx], dtype=object) if len(self.pidx) else np.zeros(0, dtype=object)
        b = np.array([self.pairs[i][3] for i in self.pidx], dtype=object) if len(self.pidx) else np.zeros(0, dtype=object)
        return a, b

    def keys(self):
        return np.array([self.pairs[i][1] for i in self.pidx], dtype=object) if len(self.pidx) else np.zeros(0, dtype=object)


def load_all(out_root, tags, variants_by_id):
    res = {}
    for tag in tags:
        d = os.path.join(out_root, "work", "arrays", tag)
        if not os.path.isdir(d):
            continue
        pairs = json.load(open(os.path.join(d, "pairs.json")))
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".npz"):
                continue
            vid = fn[:-4]
            z = np.load(os.path.join(d, fn))
            v = variants_by_id.get(vid)
            if v is None:
                continue
            res[vid] = VD(v, z["rows"], z["pidx"], z["act"], pairs, src=tag)
    # gate-only: срез прогона gate-plus (пары с ногой Gate)
    for vid, v in variants_by_id.items():
        if v["gate"] == "only":
            src = res.get(vid.replace("gate-only", "gate-plus"))
            if src is None:
                continue
            isg = np.array([("gate" in (p[2], p[3])) for p in src.pairs])
            m = isg[src.pidx] if len(src.pidx) else np.zeros(0, bool)
            act = src.act * isg[:, None].astype(np.uint8)
            res[vid] = VD(v, src.rows[m], src.pidx[m], act, src.pairs, src=src.src + "/gate-plus")
    return res


# ------------------------------------------------------------------ ряды и интервалы
def day_of_exit(vd, rows):
    return D0 + (rows[:, TF["f_out"]] // EX.MIN_DAY).astype(int)


def make_series(rows, act, d1=D_LAST, first=P0, d0=D0, pair_mask=None):
    """-> dict(build_series). rows: сделки; act: uint8 [пары, сутки]; pair_mask: булев по парам (сужает знаменатель; сделки отбираются вызывающим)."""
    nd = d1 - d0 + 1
    a = act if pair_mask is None else act[pair_mask]
    closed = rows[rows[:, TF["mtm"]] == 0] if len(rows) else rows
    mt = rows[rows[:, TF["mtm"]] == 1] if len(rows) else rows
    day_net = {}
    if len(closed):
        days = d0 + (closed[:, TF["f_out"]] // EX.MIN_DAY).astype(int)
        s = np.bincount(days - d0, weights=closed[:, TF["net"]], minlength=nd)
        day_net = {d0 + j: float(s[j]) for j in range(nd)}
    day_act = {d0 + j: int(a[:, j].sum()) for j in range(nd)}
    sig_days = [d for d in range(d0, d0 + nd) if d >= first]
    return CS.build_series(day_net, day_act, sig_days, mtm_net_sum=float(mt[:, TF["net"]].sum()) if len(mt) else 0.0, n_open=len(mt))


def interval_full(series):
    """n, блок, n_эфф, df, среднее, оба 90 %-интервала, асимметрия, доля положительных суток."""
    if not series:
        return None
    iv = AG.interval(series)
    out = dict(n=iv["n"], block=iv["block"], n_eff=iv["n_eff"], df=iv["df"], mean=iv["mean"], lo_t=iv["lo"], hi_t=iv["hi"], lo_boot=None, hi_boot=None)
    if iv["lo"] is not None:
        _, lb, hb = P.block_boot_t_interval(list(series), AG.ALPHA, CS.BOOT_B, CS.BOOT_SEED, iv["block"])
        out.update(lo_boot=lb, hi_boot=hb)
    out["skew"] = P.skewness(series)
    out["pos_share"] = float(np.mean(np.array(series) > 0))
    out["sum_per_day"] = float(sum(series) / len(series))
    return out


def split_halves(ser):
    """primary -> (ряд, ряд H1, ряд H2) по суткам primary_days."""
    pr, pd = ser["primary"], ser["primary_days"]
    if pr is None:
        return None, None, None
    h1 = [x for x, d in zip(pr, pd) if d <= H1_LAST]
    h2 = [x for x, d in zip(pr, pd) if d > H1_LAST]
    return pr, h1, h2


def trade_means(rows, mask=None):
    r = rows if mask is None else rows[mask]
    n = len(r)
    if n == 0:
        return dict(n=0, gross=None, fee=None, fund=None, net=None, cstar=None)
    g, f, u, nt = r[:, TF["gross"]].mean(), r[:, TF["fee"]].mean(), r[:, TF["fund"]].mean(), r[:, TF["net"]].mean()
    return dict(n=n, gross=float(g), fee=float(f), fund=float(u), net=float(nt), cstar=float(g + u))


def half_mask(rows):
    d = D0 + (rows[:, TF["f_out"]] // EX.MIN_DAY).astype(int)
    d = np.where(rows[:, TF["mtm"]] == 1, D_LAST, d)           # MTM - в последние сутки (H2)
    return d <= H1_LAST, d > H1_LAST


def fmt(x, nd=4):
    return "" if x is None or (isinstance(x, float) and not math.isfinite(x)) else (("%." + str(nd) + "f") % x)


# ------------------------------------------------------------------ строка основной таблицы
def main_row(vd):
    R = vd.rows
    tm = trade_means(R)
    ser = make_series(R, vd.act)
    pr, h1, h2 = split_halves(ser)
    full = interval_full(pr) if pr else None
    closed_series = ser["closed"]
    m1, m2 = half_mask(R) if len(R) else (np.zeros(0, bool), np.zeros(0, bool))
    t1, t2 = trade_means(R, m1), trade_means(R, m2)
    i1 = interval_full(h1) if h1 else None
    i2 = interval_full(h2) if h2 else None
    keys = vd.keys()
    days_tr = len({int(D0 + f // EX.MIN_DAY) for f, m in zip(R[:, TF["f_out"]], R[:, TF["mtm"]]) if m == 0}) if len(R) else 0
    row = dict(id=vd.id, fam=vd.v["fam"], base=vd.v["base"], src=vd.src, n=tm["n"], assets=len(set(keys.tolist())) if len(R) else 0, days_with_trades=days_tr,
               gross=tm["gross"], fee=tm["fee"], fund=tm["fund"], net=tm["net"], cstar=tm["cstar"],
               net_882=None if tm["cstar"] is None else tm["cstar"] - C_LIMIT, net_504=None if tm["cstar"] is None else tm["cstar"] - C_MAKER)
    for k, nm in (("mean", "yd"), ("lo_t", "yd_lo_t"), ("hi_t", "yd_hi_t"), ("lo_boot", "yd_lo_boot"), ("hi_boot", "yd_hi_boot"), ("n", "yd_n"), ("block", "yd_block"), ("n_eff", "yd_neff"),
                  ("df", "yd_df"), ("skew", "yd_skew"), ("pos_share", "yd_pos_share"), ("sum_per_day", "yd_sum_per_day")):
        row[nm] = None if full is None else full.get(k)
    mtm = ser["mtm_value"]
    row["yd_closed_mean"] = float(np.mean(closed_series)) if closed_series else None
    row["yd_mtm_part"] = mtm
    for nm, t, i in (("h1", t1, i1), ("h2", t2, i2)):
        row[nm + "_n"], row[nm + "_net"], row[nm + "_cstar"] = t["n"], t["net"], t["cstar"]
        row[nm + "_yd"] = None if i is None else i["mean"]
        row[nm + "_yd_lo_t"] = None if i is None else i["lo_t"]
        row[nm + "_yd_hi_t"] = None if i is None else i["hi_t"]
        row[nm + "_yd_lo_boot"] = None if i is None else i["lo_boot"]
        row[nm + "_yd_neff"] = None if i is None else i["n_eff"]
    return row, ser, pr


MAIN_COLS = ["id", "fam", "base", "src", "n", "assets", "days_with_trades", "gross", "fee", "fund", "net", "cstar", "net_882", "net_504", "yd", "yd_lo_t", "yd_hi_t", "yd_lo_boot", "yd_hi_boot",
             "yd_n", "yd_block", "yd_neff", "yd_df", "yd_skew", "yd_pos_share", "yd_sum_per_day", "yd_closed_mean", "yd_mtm_part", "h1_n", "h1_net", "h1_cstar", "h1_yd", "h1_yd_lo_t", "h1_yd_hi_t",
             "h1_yd_lo_boot", "h1_yd_neff", "h2_n", "h2_net", "h2_cstar", "h2_yd", "h2_yd_lo_t", "h2_yd_hi_t", "h2_yd_lo_boot", "h2_yd_neff"]


# ------------------------------------------------------------------ шум, выходы, срезы по биржам
def extra_row(vd, pr):
    R = vd.rows
    out = dict(id=vd.id)
    if not len(R):
        return out
    net = vd.net
    out["win_share"] = float(np.mean(net > 0))
    w, l_ = net[net > 0], net[net <= 0]
    out["win_loss_ratio"] = float(w.mean() / abs(l_.mean())) if len(w) and len(l_) and l_.mean() != 0 else None
    out["q05"], out["q01"], out["worst"] = float(np.quantile(net, 0.05)), float(np.quantile(net, 0.01)), float(net.min())
    if pr:
        srt = sorted(pr, reverse=True)
        out["top3days_sum"] = float(sum(srt[:3]))
        rest = sorted(pr)[:-3] if len(pr) > 3 else []
        out["mean_wo_top3days"] = float(np.mean(rest)) if rest else None
    keys = vd.keys()
    by_key = {}
    for k, x in zip(keys.tolist(), net):
        by_key[k] = by_key.get(k, 0.0) + float(x)
    order = sorted(by_key, key=by_key.get, reverse=True)
    for nm, drop in (("wo_best_asset", order[:1]), ("wo_top3_assets", order[:3])):
        dset = set(drop)
        pm = np.array([p[1] not in dset for p in vd.pairs])
        tmk = np.array([k not in dset for k in keys.tolist()])
        ser = make_series(R[tmk], vd.act, pair_mask=pm)
        out["yd_" + nm] = float(np.mean(ser["primary"])) if ser["primary"] else None
    out["best_asset"] = order[0] if order else None
    out["best_asset_net_sum"] = by_key[order[0]] if order else None
    code = R[:, TF["code"]].astype(int)
    for ci, nm in enumerate(CODES):
        m = code == ci
        out["exit_%s_n" % nm] = int(m.sum())
        out["exit_%s_net" % nm] = float(net[m].mean()) if m.any() else None
        out["exit_%s_hold" % nm] = float(R[m, TF["hold"]].mean()) if m.any() else None
    return out


def exchange_rows(vd):
    R = vd.rows
    if not len(R):
        return []
    a, b = vd.pair_exch()
    sa = R[:, TF["dir"]] > 0                       # шорт на A
    short_ex = np.where(sa, a, b)
    long_ex = np.where(sa, b, a)
    net = vd.net
    rows = []
    for ex in sorted(set(a.tolist()) | set(b.tolist())):
        m_any = (a == ex) | (b == ex)
        m_s, m_l = short_ex == ex, long_ex == ex
        for side, m in (("any", m_any), ("short", m_s), ("long", m_l)):
            if m.any():
                rows.append([vd.id, ex, side, int(m.sum()), float(net[m].mean()), float((R[m, TF["gross"]] + R[m, TF["fund"]]).mean())])
    return rows


# ------------------------------------------------------------------ парные разности с базой
def base_view(vd, ref):
    """-> (rows базы, act базы) на том же универсуме, что у варианта (или None, если сравнение не определено)."""
    v = vd.v
    if ref is None or v["fam"] == "REF":
        return None
    R, act = ref.rows, ref.act
    if v["excl"]:
        pm = np.array([v["excl"] not in (p[2], p[3]) for p in ref.pairs])
        m = pm[ref.pidx] if len(ref.pidx) else np.zeros(0, bool)
        return R[m], act * pm[:, None].astype(np.uint8)
    if v["terc"]:
        m = R[:, TF["terc"]] == v["terc"]
        return R[m], vd.act
    if v["univ"] != "main":
        return R, act
    if (v["th"], v["k"], v["p"]) != (40.0, 3, 3):
        return R, act
    return R, act


def diff_stats(vd, Rb, actb, pre=""):
    """Парная разность y_d варианта и базы (Rb, actb) по общим суткам: полный ряд и половины, 90 %-интервал блочного t; разность net на сделку (полный ряд, H1, H2)."""
    sv = make_series(vd.rows, vd.act)
    sb = make_series(Rb, actb)
    out = {}
    if not (sv["primary"] and sb["primary"]):
        return out
    mv = dict(zip(sv["primary_days"], sv["primary"]))
    mb = dict(zip(sb["primary_days"], sb["primary"]))
    days = sorted(set(mv) & set(mb))
    for nm, sel in (("all", days), ("h1", [d for d in days if d <= H1_LAST]), ("h2", [d for d in days if d > H1_LAST])):
        ds = [mv[d] - mb[d] for d in sel]
        iv = AG.interval(ds) if len(ds) >= 3 else None
        out[pre + nm + "_n"] = len(ds)
        out[pre + nm + "_diff_mean"] = float(np.mean(ds)) if ds else None
        out[pre + nm + "_diff_lo_t"] = None if iv is None else iv["lo"]
        out[pre + nm + "_diff_hi_t"] = None if iv is None else iv["hi"]
    tv, tb = trade_means(vd.rows), trade_means(Rb)
    out[pre + "net_diff"] = None if tv["net"] is None or tb["net"] is None else tv["net"] - tb["net"]
    out[pre + "n_base"] = tb["n"]
    m1, m2 = half_mask(vd.rows) if len(vd.rows) else (np.zeros(0, bool), np.zeros(0, bool))
    b1, b2 = half_mask(Rb) if len(Rb) else (np.zeros(0, bool), np.zeros(0, bool))
    for nm, mv_, mb_ in (("h1", m1, b1), ("h2", m2, b2)):
        a_, b_ = trade_means(vd.rows, mv_), trade_means(Rb, mb_)
        out[pre + nm + "_net_diff"] = None if a_["net"] is None or b_["net"] is None else a_["net"] - b_["net"]
    return out


def paired_rows(vd, ref):
    """База того же универсума (протокол §4 п. 7) и, для подмножеств/иных универсумов, ВТОРАЯ разность - с полной базой REF (full_*): для E-X и терцилей разность с базой того же универсума почти нулевая
    по построению, а вопрос семейства («что даёт подмножество») читается по full_*."""
    bv = base_view(vd, ref)
    if bv is None:
        return None
    Rb, actb = bv
    if vd.src and vd.src.startswith("main") and den_same(vd):
        actb = vd.act
    out = dict(id=vd.id, base=ref.id, n_variant=len(vd.rows))
    out.update(diff_stats(vd, Rb, actb))
    if vd.v["excl"] or vd.v["terc"] or vd.v["univ"] != "main":
        out.update(diff_stats(vd, ref.rows, ref.act, pre="full_"))
    return out


def den_same(vd):
    return EX.den_mode(vd.v) in ("ref", "ref_terc")


PAIRED_COLS = ["id", "base", "n_variant", "n_base", "net_diff", "h1_net_diff", "h2_net_diff", "all_n", "all_diff_mean", "all_diff_lo_t", "all_diff_hi_t", "h1_n", "h1_diff_mean", "h1_diff_lo_t",
               "h1_diff_hi_t", "h2_n", "h2_diff_mean", "h2_diff_lo_t", "h2_diff_hi_t",
               "full_n_base", "full_net_diff", "full_h1_net_diff", "full_h2_net_diff", "full_all_n", "full_all_diff_mean", "full_all_diff_lo_t", "full_all_diff_hi_t", "full_h1_diff_mean", "full_h1_diff_lo_t",
               "full_h1_diff_hi_t", "full_h2_diff_mean", "full_h2_diff_lo_t", "full_h2_diff_hi_t"]


# ------------------------------------------------------------------ диагностики §10
def funding_mechanism(vd):
    """Для сделок базы: средний факт-фандинг по паре (биржа шорта, биржа лонга), по знаку Ê (T и q90), корреляция Ê с фактом."""
    R = vd.rows
    if not len(R):
        return []
    a, b = vd.pair_exch()
    sa = R[:, TF["dir"]] > 0
    short_ex, long_ex = np.where(sa, a, b), np.where(sa, b, a)
    fund = R[:, TF["fund"]]
    rows = []
    for ex in sorted(set(a.tolist()) | set(b.tolist())):
        for side, ex_arr in (("short", short_ex), ("long", long_ex)):
            m = ex_arr == ex
            if m.any():
                rows.append([vd.id, "биржа " + side, ex, int(m.sum()), float(fund[m].mean()), float(fund[m].sum())])
    for nm in ("ehat_T", "ehat_q"):
        e = R[:, TF[nm]]
        ok = np.isfinite(e)
        for lab, m in (("Ê<0", ok & (e < 0)), ("Ê=0", ok & (e == 0)), ("Ê>0", ok & (e > 0))):
            if m.any():
                rows.append([vd.id, nm + " знак", lab, int(m.sum()), float(fund[m].mean()), float(fund[m].sum())])
        if ok.sum() > 3 and np.std(e[ok]) > 0 and np.std(fund[ok]) > 0:
            rows.append([vd.id, nm + " корреляция с фактом", "pearson", int(ok.sum()), float(np.corrcoef(e[ok], fund[ok])[0, 1]), None])
            nz = ok & (e != 0) & (fund != 0)
            if nz.any():
                rows.append([vd.id, nm + " совпадение знака с фактом (оба ненулевые)", "доля", int(nz.sum()), float(np.mean(np.sign(e[nz]) == np.sign(fund[nz]))), None])
    nzf = fund != 0
    rows.append([vd.id, "сделки с ненулевым фандингом", "доля", len(R), float(nzf.mean()), None])
    return rows


def spread_path_rows(vd):
    """Путь dir·(s − b_in) на +0/5/15/30/60/120/240 мин после входа для сделок с выходом S1 и TO (закрывает «не установлено (а)»); срез: с ногой Bybit / без."""
    R = vd.rows
    if not len(R):
        return []
    a, b = vd.pair_exch()
    byb = (a == "bybit") | (b == "bybit")
    code = R[:, TF["code"]].astype(int)
    rows = []
    for cn in ("S1", "TO", "CV"):
        for lab, m0 in (("все", np.ones(len(R), bool)), ("с ногой Bybit", byb), ("без Bybit", ~byb)):
            m = (code == CODES.index(cn)) & m0
            if not m.any():
                continue
            row = [vd.id, cn, lab, int(m.sum())]
            for k in EX.OFFS:
                x = R[m, TF["sp%d" % k]]
                row.append(float(np.nanmean(x)) if np.isfinite(x).any() else None)
            rows.append(row)
    return rows


# ------------------------------------------------------------------ счётчик испытаний, M_эфф, различные наборы сделок
def trade_set_hash(vd):
    if not len(vd.rows):
        return "empty"
    key = np.stack([vd.pidx.astype(np.float64), vd.rows[:, TF["t_sig"]], vd.rows[:, TF["f_out"]]], axis=1)
    return hashlib.sha256(np.ascontiguousarray(key[np.lexsort(key.T[::-1])]).tobytes()).hexdigest()[:16]


def cmd_tables(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    params = os.path.join(a.code_root, EX.PARAMS_REL)
    variants = {v["id"]: v for v in EX.parse_variants(params)}
    suppl = os.path.join(a.code_root, EX.SUPPL_REL)
    if os.path.isfile(suppl):
        variants.update({v["id"]: v for v in EX.parse_variants(suppl)})
    for p_ in (a.extra_params or "").split(","):
        if p_:
            variants.update({v["id"]: v for v in EX.parse_variants(p_)})
    data = load_all(a.out, a.tags.split(","), variants)
    tdir = os.path.join(a.out, "tables")
    order = sorted(data, key=lambda k: (data[k].v["stage"], {"REF": 0, "A": 1, "B": 2, "C": 3, "Cu": 3.5, "D": 4, "E": 5, "F": 6}.get(data[k].v["fam"], 9), k))
    mains, extras, exch, paired, fmech, spath = [], [], [], [], [], []
    series_by = {}
    for vid in order:
        vd = data[vid]
        row, ser, pr = main_row(vd)
        mains.append(row)
        series_by[vid] = (ser["primary_days"], ser["primary"])
        extras.append(extra_row(vd, pr))
        exch += exchange_rows(vd)
        ref = data.get("REF-" + vd.v["base"])
        p_ = paired_rows(vd, ref)
        if p_:
            paired.append(p_)
        if vd.v["fam"] == "REF":
            fmech += funding_mechanism(vd)
        if vd.v["fam"] == "REF" or (vd.v["fam"] == "A" and vd.v["lam"] == "inf" and vd.v["s0"] is None and vd.v["ecap"] == "base"):
            spath += spread_path_rows(vd)
    hashes = {vid: trade_set_hash(data[vid]) for vid in order}
    mat_days = None
    ids = [v for v in order if series_by[v][1] and data[v].v["univ"] == "main" and data[v].v["fam"] != "REF"] + [v for v in order if data[v].v["fam"] == "REF"]
    common = sorted(set.intersection(*[set(series_by[v][0]) for v in ids])) if ids else []
    m_eff = None
    if len(common) >= 3 and len(ids) >= 2:
        mat = np.array([[dict(zip(*series_by[v]))[d] for v in ids] for d in common])
        m_eff = float(CS.m_eff(mat)[0])
    XG.write_csv(os.path.join(tdir, "variants_main.csv"), MAIN_COLS, [[r.get(c) for c in MAIN_COLS] for r in mains])
    ecols = sorted({k for r in extras for k in r} - {"id"})
    XG.write_csv(os.path.join(tdir, "variants_extra.csv"), ["id"] + ecols, [[r.get("id")] + [r.get(c) for c in ecols] for r in extras])
    XG.write_csv(os.path.join(tdir, "by_exchange.csv"), ["id", "exchange", "side", "n", "net_mean", "gross_plus_fund_mean"], exch)
    XG.write_csv(os.path.join(tdir, "paired_diff.csv"), PAIRED_COLS, [[r.get(c) for c in PAIRED_COLS] for r in paired])
    XG.write_csv(os.path.join(tdir, "funding_mechanism.csv"), ["id", "срез", "значение", "n", "среднее", "сумма"], fmech)
    XG.write_csv(os.path.join(tdir, "spread_path.csv"), ["id", "выход", "срез", "n"] + ["sp%d" % k for k in EX.OFFS], spath)
    cnt_all = {}
    for tag in a.tags.split(","):
        p_ = os.path.join(a.out, "out", tag, "counters.json")
        if os.path.isfile(p_):
            cnt_all.update({k: v for k, v in json.load(open(p_)).items() if k != "_banner"})
    XG.write_json(os.path.join(tdir, "trial_count.json"), dict(
        variants=len(order), by_family=dict(Counter(data[v].v["fam"] for v in order)), by_stage=dict(Counter(data[v].v["stage"] for v in order)), distinct_trade_sets=len(set(hashes.values())),
        m_eff_daily_yd=m_eff, m_eff_note="(Σλ)²/Σλ² по суточным y_d вариантов основной вселенной (P-38 m_eff_rule); в поправку не идёт; общих суток %d, вариантов %d" % (len(common), len(ids)),
        trade_set_hashes=hashes, cells_read_per_row=3, read_columns_per_variant="~19 срезов/справок: полный ряд, H1, H2, биржи, выходы, шум, границы издержек, парные разности", counters=cnt_all))
    print("таблицы записаны: вариантов %d, различных наборов сделок %d, M_эфф %s" % (len(order), len(set(hashes.values())), "%.2f" % m_eff if m_eff else "—"))


def subfam(r):
    """Подсемейство строки для сводки: E - по префиксу id; остальные - по семейству."""
    i, fam = r["id"], r["fam"]
    if fam != "E":
        return fam
    for key, nm in (("-no-", "E-X (без биржи)"), ("noshort", "E-SB (без шорта на Bybit)"), ("-terc", "E-T (терцили оборота)"), ("-out", "E-O (выбросная нога S/L)"), ("-ox", "E-OX (без выбросных ног)"),
                    ("gate", "E-G (с Gate)"), ("-eq-", "E-EQ (акции+ETF)")):
        if key in i:
            return nm
    return fam


def cmd_summary(a):
    """Сводка по семействам (главная таблица отчёта): число вариантов; лучшие и худшие по нижней границе y_d и по net на сделку; сколько вариантов с net > 0 в полном ряду и в обеих половинах; разделение половин."""
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    with open(os.path.join(a.out, "tables", "variants_main.csv"), encoding="utf-8", newline="") as f:
        f.readline()
        import csv
        rows = list(csv.DictReader(f))

    def fv(r, k):
        try:
            return float(r[k])
        except (TypeError, ValueError):
            return None
    groups = {}
    for r in rows:
        if r["id"].endswith("-d0"):
            continue                                     # d0 в ранжирование не входит (§4.1)
        groups.setdefault(subfam(r), []).append(r)
    out = []
    for g, rs in groups.items():
        ok_lo = [r for r in rs if fv(r, "yd_lo_t") is not None]
        best = max(ok_lo, key=lambda r: fv(r, "yd_lo_t")) if ok_lo else None
        worst = min(ok_lo, key=lambda r: fv(r, "yd_lo_t")) if ok_lo else None
        bn = max(rs, key=lambda r: fv(r, "net") if fv(r, "net") is not None else -1e9)
        wn = min(rs, key=lambda r: fv(r, "net") if fv(r, "net") is not None else 1e9)
        both = sum(1 for r in rs if all((fv(r, k) or 0) > 0 for k in ("net", "h1_net", "h2_net")))
        lo_pos = sum(1 for r in rs if (fv(r, "yd_lo_t") or -1) > 0)

        def cells(r):
            return [r["id"], fv(r, "n"), fv(r, "net"), fv(r, "yd_lo_t"), fv(r, "h1_net"), fv(r, "h2_net"), fv(r, "h1_yd"), fv(r, "h2_yd")]
        out.append([g, len(rs), both, lo_pos] + cells(best) + cells(worst) + cells(bn)[:3] + cells(wn)[:3])
    hdr = ["семейство", "вариантов", "net>0 в ряду и обеих половинах", "нижняя граница y_d > 0"]
    for p_ in ("лучший_по_lo", "худший_по_lo"):
        hdr += ["%s_id" % p_, "n", "net", "yd_lo_t", "h1_net", "h2_net", "h1_yd", "h2_yd"]
    for p_ in ("лучший_по_net", "худший_по_net"):
        hdr += ["%s_id" % p_, "n", "net"]
    XG.write_csv(os.path.join(a.out, "tables", "family_summary.csv"), hdr, out)
    print("сводка: семейств %d" % len(out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("tables", "null", "summary"):
        s = sp.add_parser(name)
        s.add_argument("--out", default=XG.c2x_home())
        s.add_argument("--tags", default="main")
        s.add_argument("--code-root", default=os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "..")))
        s.add_argument("--extra-params", default=None)
    sp.choices["null"].add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    if a.cmd == "tables":
        cmd_tables(a)
    elif a.cmd == "summary":
        cmd_summary(a)
    else:
        import explore_null as EN
        EN.cmd_null(a)


if __name__ == "__main__":
    main()
