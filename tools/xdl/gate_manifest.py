#!/usr/bin/env python3
"""Манифест готовности Gate (П-38 §3.4): шесть проверок rows / gaps / units / open_semantics / funding / classes ТОЛЬКО на формировании
(сутки от 2020-01-01 UTC 2465…2478 = 01.10-14.10.2026), торговое окно (сутки >= 2479) не читается: все чтения идут через границу FORM_END_MS
(потоковое чтение csv останавливается на первой строке позже формирования, панели читаются по смещению не дальше сутки 2478, жёсткий assert).
Запускается как `gate.py --mode manifest` (см. gate.py); результат - файл ~/xdl9/gate_ready формата «имя=ok» (его читают conv_gate.manifest_valid и
conv_guard.write_k0). Каждая строка `имя=ok` пишется только если соответствующая проверка прошла; иначе строка `имя=FAIL: причина`, а файл помечается
невалидным (манифест валиден, только если все шесть строк ok). Старый маркер переименовывается в gate_ready.old, не удаляется.

Определения проверок (все числа - измерение; назначенное - только перечисленное в разделе «НАЗНАЧЕНО» ниже и помечено в манифесте):
  rows            все контракты instruments.csv имеют файл свечей, файл фандинга, панель и месячный файл архива; строки формирования файла свечей = строки
                  архива (те же минуты, те же o/h/l/c, v = контракты × quanto); число валидных минут панели = число строк файла с v > 0 и c > 0; значения
                  панели (open, close) равны значениям файла.
  gaps            строки файла упорядочены, без дублей, на сетке минуты; заголовок каждой панели: первая минута не раньше сутки 2465 (2026-10-01 00:00 UTC),
                  у панели контракта, торговавшего в первую минуту формирования, она равна ровно этой минуте (панель - спан от первой до последней
                  валидной минуты, не всегда 50400); пик корреляции минутных доходностей Gate с другими биржами по топ-20 контрактам Gate (по обороту формирования, с основным
                  контрактом на Binance) на лаге 0 при лагах -3..+3 у ВСЕХ пар (актив, биржа) с n_ret >= 300 (n_ret_min из П-37, назначен F5).
  units           множитель контракта: у файла v УЖЕ в базовых единицах (контракты × quanto_multiplier, так пишет gate.py); независимо от файла оборот из месячного
                  архива (close × контракты × quanto из instruments.csv) равен c × v файла на каждом контракте (относительное расхождение суммы по формированию <= 1e-9),
                  множитель оборота на v файла равен 1; сверка с оборотом других бирж: (а) по 20 крупнейшим активам Gate отношение оборот Gate / оборот Binance за
                  формирование не отклоняется от медианы по 20 активам в 10 и более раз; (б) по ВСЕМ активам крипто-класса с основным контрактом на Binance: у каждого
                  значения quanto, которое встречается у >= 5 активов, медиана отклонения log10(отношения) от общей медианы по модулю меньше 1 (системная ошибка
                  множителя сдвигала бы всю группу на степень десяти, >= ×10); выбросы по отдельным активам печатаются (описательно). quanto - не обязательно степень
                  десяти (у ACH_USDT 11, оборот согласован с Binance).
  open_semantics  доля минут open[t] = close[t-1] (при валидных close[t-1], open[t], close[t]) по всем панелям Gate за формирование: медиана, квантили 10 % и
                  90 %; метка «цепочка» при медиане >= 0.999 (как у Bybit/BingX/Bitget/Kraken/Lighter, у которых params измерил 1.000), иначе «первая сделка».
  funding         строки фандинга формирования в файле = строки месячного архива funding_applies (то же число, ставка равна, минута сетки равна после округления до
                  ближайшей минуты); знак и единицы: суточные суммы ставок Gate против Binance по активам формирования имеют положительные связь (Пирсон) и наклон,
                  наклон лежит в [0.1, 10].
  classes         каждый контракт instruments.csv относится ровно к одному классу из crypto/equity/index/metal/energy/fx (commodity и other - вне, §3.4);
                  class согласован с class_src; классы C2: crypto→крипта, equity→акции+ETF, index→индексы, metal+energy→металлы+энергия, fx→фиат.
НАЗНАЧЕНО (исполнитель G1 по заданию CEO, подтвердить): допуски 1e-9 (числовое равенство с учётом float), [0.1, 10] (наклон фандинга и отклонение оборота,
границы - степень десяти), порог цепочки 0.999, число активов в сверках 20; n_ret_min = 300 (из П-37).
"""
import collections
import csv
import gzip
import hashlib
import json
import math
import multiprocessing
import os
import struct
import sys
import time

import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_MS = 60_000
D0, D1 = 2465, 2478                       # формирование; D1 - жёсткая граница чтения (окно >= 2479 не читается)
FORM_START_MS = GRID0_MS + D0 * 1440 * MIN_MS
FORM_END_MS = GRID0_MS + (D1 + 1) * 1440 * MIN_MS
N_FORM = (D1 - D0 + 1) * 1440
N_RET_MIN = 300
CHECKS = ("rows", "gaps", "units", "open_semantics", "funding", "classes")
CLS_C2 = {"crypto": "крипта", "equity": "акции+ETF", "index": "индексы", "metal": "металлы+энергия", "energy": "металлы+энергия", "fx": "фиат"}
TOL = 1e-9
TOP_N = 20
CHAIN_TH = 0.999


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sha256_lf(path):
    """sha256 содержимого с переводами строки LF (копии файлов на Deck и в git могут различаться CRLF/LF)."""
    return hashlib.sha256(open(path, "rb").read().replace(b"\r\n", b"\n")).hexdigest()


# ------------------------------------------------------------------------------------------------------ чтение (только формирование)
def read_candles_form(path):
    """Файл свечей xdl (t_open_ms,o,h,l,c,v,quote_v) -> словарь массивов за формирование; чтение останавливается на первой строке позже формирования."""
    ts, o, h, l, c, v = [], [], [], [], [], []
    if not os.path.isfile(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            t = int(line[:line.index(",")])
            if t >= FORM_END_MS:
                break
            if t < FORM_START_MS:
                continue
            z = line.rstrip("\n").split(",")
            ts.append(t)
            o.append(z[1]); h.append(z[2]); l.append(z[3]); c.append(z[4]); v.append(z[5])
    return dict(t=np.array(ts, dtype=np.int64), o=np.array(o, dtype=np.float64), h=np.array(h, dtype=np.float64), l=np.array(l, dtype=np.float64),
                c=np.array(c, dtype=np.float64), v=np.array(v, dtype=np.float64))


def read_archive_form(path):
    """Месячный архив Gate (t(с),объём_контрактов,close,high,low,open; без заголовка) -> массивы за формирование; остановка на первой строке позже."""
    if not os.path.isfile(path):
        return None
    ts, vol, c, h, l, o = [], [], [], [], [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for line in f:
            if not line.strip():
                continue
            t = int(line[:line.index(",")]) * 1000
            if t >= FORM_END_MS:
                break
            if t < FORM_START_MS:
                continue
            z = line.rstrip("\n").split(",")
            ts.append(t)
            vol.append(z[1]); c.append(z[2]); h.append(z[3]); l.append(z[4]); o.append(z[5])
    return dict(t=np.array(ts, dtype=np.int64), vol=np.array(vol, dtype=np.float64), c=np.array(c, dtype=np.float64), h=np.array(h, dtype=np.float64),
                l=np.array(l, dtype=np.float64), o=np.array(o, dtype=np.float64))


def read_funding_file_form(path):
    """Файл фандинга xdl (time_ms,rate,interval_h) за формирование -> (время_мс, ставка, интервал)."""
    if not os.path.isfile(path):
        return None
    ts, rs, iv = [], [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            z = line.rstrip("\n").split(",")
            t = int(z[0])
            if t >= FORM_END_MS:
                break
            if t >= FORM_START_MS:
                ts.append(t); rs.append(float(z[1])); iv.append(float(z[2]) if len(z) > 2 and z[2] != "" else float("nan"))
    return np.array(ts, dtype=np.int64), np.array(rs), np.array(iv)


def read_funding_archive_form(path):
    """Архив funding_applies (t(с),ставка; без заголовка) за формирование -> (время_мс, ставка)."""
    if not os.path.isfile(path):
        return None
    ts, rs = [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for line in f:
            if not line.strip():
                continue
            z = line.rstrip("\n").split(",")
            t = int(float(z[0])) * 1000
            if t >= FORM_END_MS:
                break
            if t >= FORM_START_MS:
                ts.append(t); rs.append(float(z[1]))
    return np.array(ts, dtype=np.int64), np.array(rs)


def read_panel_form(path):
    """Панель .sap (SAP1: 24 байта заголовка, затем open[n], close[n] float64 LE) -> (first, n, open, close за формирование). Читаются только байты формирования."""
    with open(path, "rb") as f:
        h = f.read(24)
        if h[:4] != b"SAP1":
            raise ValueError("не SAP1: " + path)
        first = struct.unpack("<q", h[8:16])[0]
        n = struct.unpack("<Q", h[16:24])[0]
        m0, m1 = D0 * 1440, (D1 + 1) * 1440
        lo, hi = max(m0, first), min(m1, first + n)
        assert hi <= m1
        o = np.full(N_FORM, np.nan)
        c = np.full(N_FORM, np.nan)
        if hi > lo:
            cnt = hi - lo
            f.seek(24 + (lo - first) * 8)
            o[lo - m0:hi - m0] = np.frombuffer(f.read(cnt * 8), dtype="<f8")
            f.seek(24 + n * 8 + (lo - first) * 8)
            c[lo - m0:hi - m0] = np.frombuffer(f.read(cnt * 8), dtype="<f8")
    return first, n, o, c


def rel_diff(a, b):
    return np.abs(a - b) / np.maximum(np.maximum(np.abs(a), np.abs(b)), 1e-300)


# ------------------------------------------------------------------------------------------------------ проверка одного контракта
def contract_task(args):
    out_root, xdl9, row = args
    sym, native = row["symbol"], row["native_id"]
    q = float(row["contract_size"] or "1")
    rec = dict(symbol=sym, quanto=q)
    cf = read_candles_form(os.path.join(out_root, "gate", "candles", sym + ".csv.gz"))
    ar = read_archive_form(os.path.join(out_root, "gate", "_raw", "candlesticks_1m", "202610", native + ".csv.gz"))
    fin = read_funding_file_form(os.path.join(out_root, "gate", "funding", sym + ".csv.gz"))
    fa = read_funding_archive_form(os.path.join(out_root, "gate", "_raw", "funding_applies", "202610", native + ".csv.gz"))
    pp = os.path.join(xdl9, "panels", "gate", sym + ".sap")
    rec.update(has_candles=cf is not None, has_archive=ar is not None, has_funding=fin is not None, has_fund_archive=fa is not None, has_panel=os.path.isfile(pp))
    if cf is None or ar is None:
        return rec
    t, a = cf["t"], ar
    rec["n_file"], rec["n_raw"] = int(len(t)), int(len(a["t"]))
    rec["ordered"] = bool(np.all(np.diff(t) > 0)) if len(t) > 1 else True
    rec["off_grid"] = int(np.sum((t - GRID0_MS) % MIN_MS != 0))
    rec["raw_ordered"] = bool(np.all(np.diff(a["t"]) > 0)) if len(a["t"]) > 1 else True
    rec["same_minutes"] = bool(len(t) == len(a["t"]) and np.array_equal(t, a["t"]))
    if rec["same_minutes"] and len(t):
        rec["max_rel_o"] = float(rel_diff(cf["o"], a["o"]).max())
        rec["max_rel_h"] = float(rel_diff(cf["h"], a["h"]).max())
        rec["max_rel_l"] = float(rel_diff(cf["l"], a["l"]).max())
        rec["max_rel_c"] = float(rel_diff(cf["c"], a["c"]).max())
        v_raw = a["vol"] * q                                   # контракты × quanto из instruments.csv (не из файла свечей)
        rec["max_rel_v"] = float(rel_diff(cf["v"], v_raw).max())
        tf, tr_ = float((cf["c"] * cf["v"]).sum()), float((a["c"] * a["vol"] * q).sum())
        rec["turn_file"], rec["turn_raw"] = tf, tr_
        rec["turn_rel"] = float(abs(tf - tr_) / max(abs(tr_), 1e-300)) if tr_ > 0 else 0.0
    rec["n_v0"] = int(np.sum(cf["v"] == 0))
    rec["first_minute_row"] = bool(len(t) and t[0] == FORM_START_MS and cf["v"][0] > 0 and cf["c"][0] > 0)
    pos = (cf["v"] > 0) & (cf["c"] > 0)
    rec["n_pos"] = int(pos.sum())
    if rec["has_panel"]:
        first, n, po, pc = read_panel_form(pp)
        rec["panel_first"], rec["panel_n"] = int(first), int(n)
        rec["panel_valid"] = int(np.isfinite(pc).sum())
        idx = ((t - FORM_START_MS) // MIN_MS)[pos]
        m_c = np.zeros(N_FORM, bool)
        m_c[idx] = True
        rec["panel_valid_eq"] = bool(np.array_equal(np.isfinite(pc), m_c) and np.array_equal(np.isfinite(po), m_c))
        if idx.size:
            rec["panel_max_rel_c"] = float(rel_diff(pc[idx], cf["c"][pos]).max())
            rec["panel_max_rel_o"] = float(rel_diff(po[idx], cf["o"][pos]).max())
        both = np.isfinite(po[1:]) & np.isfinite(pc[:-1]) & np.isfinite(pc[1:])
        rec["open_n_both"] = int(both.sum())
        rec["open_n_eq"] = int(np.sum(po[1:][both] == pc[:-1][both]))
    if fin is not None and fa is not None:
        tf_, rf_, _ = fin
        ta_, ra_ = fa
        rec["fund_n_file"], rec["fund_n_raw"] = int(len(tf_)), int(len(ta_))
        if len(tf_) == len(ta_):
            rec["fund_rate_eq"] = bool(np.array_equal(rf_, ra_))
            rec["fund_min_eq"] = bool(np.array_equal(np.rint(tf_ / MIN_MS), np.rint(ta_ / MIN_MS)))
            rec["fund_exact_t"] = int(np.sum(tf_ == ta_))
            rec["fund_max_dt_ms"] = int(np.abs(tf_ - ta_).max()) if len(tf_) else 0
    return rec


# ------------------------------------------------------------------------------------------------------ выравнивание и сверки с другими биржами
def _stem(file):
    return file[:-7] if file.endswith(".csv.gz") else file


def load_asset_idx(xdl9, ex):
    p = os.path.join(xdl9, "asset", ex + ".idx.csv")
    return {_stem(r["file"]): r for r in csv.DictReader(open(p, encoding="utf-8", newline=""))}


def alignment_and_units_cross(xdl9, out_root, rows, recs, log):
    """Топ-20 контрактов Gate (крипта, по обороту формирования, с основным контрактом на Binance): лаговая корреляция с другими биржами и отношение оборотов."""
    keyf = os.path.join(xdl9, "inst_gate", "gate_asset_key.csv")
    keyed = {r["symbol"]: r for r in csv.DictReader(open(keyf, encoding="utf-8", newline=""))}
    crypto = {r["symbol"] for r in rows if r["class"] == "crypto"}
    idx = {ex: load_asset_idx(xdl9, ex) for ex in ("binance", "bybit", "okx", "bingx", "bitget", "aster", "kraken", "lighter")}
    gate_turn = {r["symbol"]: recs[r["symbol"]].get("turn_file", 0.0) for r in rows}
    cand = []
    for s in crypto:
        mains = dict(x.split(":", 1) for x in keyed.get(s, {}).get("main_contracts_same_asset_id", "").split(";") if ":" in x)
        if "binance" in mains and gate_turn.get(s, 0) > 0:
            cand.append((gate_turn[s], s, mains))
    cand.sort(key=lambda z: (-z[0], z[1]))
    top = cand[:TOP_N]
    # ----- оборот: Gate / Binance за формирование (idx.turnover = Σ quote_v либо Σ c·v по минутам формирования, как у остальных бирж)
    ratios = []
    for tg, s, mains in top:
        bin_sym = mains["binance"]
        rb = idx["binance"].get(bin_sym)
        tb = float(rb["turnover"]) if rb else float("nan")
        ratios.append(dict(symbol=s, binance=bin_sym, gate_turnover=tg, binance_turnover=tb, ratio=tg / tb if tb > 0 else float("nan")))
    lr = np.array([math.log10(z["ratio"]) for z in ratios if z["ratio"] == z["ratio"] and z["ratio"] > 0])
    med = float(np.median(lr)) if len(lr) else float("nan")
    for z in ratios:
        z["log10_dev_from_median"] = (math.log10(z["ratio"]) - med) if z["ratio"] == z["ratio"] and z["ratio"] > 0 else float("nan")
    cross_top_ok = bool(len(lr) == len(top) and len(top) > 0 and np.all(np.abs(lr - med) < 1.0))
    # ----- все активы крипто-класса с основным контрактом на Binance: группы по quanto
    allr = []
    for r in rows:
        s_ = r["symbol"]
        if r["class"] != "crypto":
            continue
        mains_ = dict(x.split(":", 1) for x in keyed.get(s_, {}).get("main_contracts_same_asset_id", "").split(";") if ":" in x)
        rb = idx["binance"].get(mains_.get("binance", ""))
        tg_, tb_ = gate_turn.get(s_, 0.0), (float(rb["turnover"]) if rb else 0.0)
        if tg_ > 0 and tb_ > 0:
            allr.append((s_, r["contract_size"], math.log10(tg_ / tb_)))
    medall = float(np.median([z[2] for z in allr])) if allr else float("nan")
    byq = collections.defaultdict(list)
    for s_, q_, lv in allr:
        byq[q_].append(lv - medall)
    buckets = {q_: dict(n=len(v), median_dev=float(np.median(v))) for q_, v in sorted(byq.items(), key=lambda kv: float(kv[0]))}
    bucket_bad = [q_ for q_, b in buckets.items() if b["n"] >= 5 and abs(b["median_dev"]) >= 1.0]
    outliers = sorted(((s_, q_, lv - medall) for s_, q_, lv in allr if abs(lv - medall) >= 1.0), key=lambda z: -abs(z[2]))
    cross_ok = cross_top_ok and not bucket_bad and len(allr) > 0
    # ----- выравнивание: корреляция минутных доходностей на лагах -3..+3 (лаг k: corr(r_ex[t], r_gate[t+k]))
    align = []
    bad = 0
    measurable = 0
    for tg, s, mains in top:
        _, _, go, gc = read_panel_form(os.path.join(xdl9, "panels", "gate", s + ".sap"))
        rg = np.full(N_FORM, np.nan)
        with np.errstate(all="ignore"):
            rg[1:] = np.log(gc[1:] / gc[:-1])
        for ex, sy in sorted(mains.items()):
            pth = os.path.join(xdl9, "panels", ex, sy + ".sap")
            if not os.path.isfile(pth):
                continue
            _, _, eo, ec = read_panel_form(pth)
            re_ = np.full(N_FORM, np.nan)
            with np.errstate(all="ignore"):
                re_[1:] = np.log(ec[1:] / ec[:-1])
            cs = {}
            for k in range(-3, 4):
                a_ = re_[max(0, -k):N_FORM - max(0, k)]
                b_ = rg[max(0, k):N_FORM - max(0, -k)]
                m = np.isfinite(a_) & np.isfinite(b_)
                n = int(m.sum())
                if n >= 3 and a_[m].std() > 0 and b_[m].std() > 0:
                    cs[k] = (float(np.corrcoef(a_[m], b_[m])[0, 1]), n)
                else:
                    cs[k] = (float("nan"), n)
            n0 = cs[0][1]
            best = max(cs, key=lambda k: cs[k][0] if cs[k][0] == cs[k][0] else -2)
            ok_meas = n0 >= N_RET_MIN
            measurable += int(ok_meas)
            bad += int(ok_meas and best != 0)
            align.append(dict(gate=s, ex=ex, sym=sy, n_ret=n0, peak_lag=best, corr0=cs[0][0], measurable=ok_meas, corr_by_lag={str(k): cs[k][0] for k in cs}))
    log("units: топ-%d активов, log10 отношения оборотов Gate/Binance: медиана %.3f, максимум |отклонение| %.3f; все активы %d, групп quanto со сдвигом >= 1: %s, выбросов %d" % (
        len(top), med, float(np.abs(lr - med).max()) if len(lr) else float("nan"), len(allr), bucket_bad, len(outliers)))
    log("gaps: выравнивание: пар (актив, биржа) %d, измеримых %d (n_ret >= %d), пик не на лаге 0: %d" % (len(align), measurable, N_RET_MIN, bad))
    return dict(top=[(s) for _, s, _ in top], ratios=ratios, ratios_median_log10=med, cross_ok=cross_ok, cross_top_ok=cross_top_ok, align=align, align_bad=bad,
                align_measurable=measurable, all_assets=len(allr), all_median_log10=medall, buckets_by_quanto=buckets, bucket_bad=bucket_bad,
                outliers_abs_dev_ge_1=[dict(symbol=a_, quanto=b_, dev=c_) for a_, b_, c_ in outliers])


def funding_vs_binance(xdl9, out_root, keyed, rows, log):
    """Суточные суммы ставок Gate против Binance (фактические файлы xdl, формирование) по активам с основным контрактом на Binance."""
    gx, bx = [], []
    n_assets = 0
    for r in rows:
        s = r["symbol"]
        k = keyed.get(s)
        if not k or r["class"] != "crypto":
            continue
        mains = dict(x.split(":", 1) for x in k.get("main_contracts_same_asset_id", "").split(";") if ":" in x)
        if "binance" not in mains:
            continue
        g = read_funding_file_form(os.path.join(out_root, "gate", "funding", s + ".csv.gz"))
        b = read_funding_file_form(os.path.join(out_root, "binance", "funding", mains["binance"] + ".csv.gz"))
        if g is None or b is None or not len(g[0]) or not len(b[0]):
            continue
        dg = np.zeros(D1 - D0 + 1)
        db = np.zeros(D1 - D0 + 1)
        np.add.at(dg, ((g[0] - FORM_START_MS) // (1440 * MIN_MS)).astype(int), g[1])
        np.add.at(db, ((b[0] - FORM_START_MS) // (1440 * MIN_MS)).astype(int), b[1])
        gx.extend(dg.tolist())
        bx.extend(db.tolist())
        n_assets += 1
    x, y = np.array(bx), np.array(gx)
    res = dict(n_assets=n_assets, n_points=int(len(x)))
    if len(x) > 2 and x.std() > 0 and y.std() > 0:
        res["corr"] = float(np.corrcoef(x, y)[0, 1])
        res["slope"] = float(((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum())
    log("funding: Gate против Binance: активов %d, точек (актив, сутки) %d, корреляция %s, наклон %s" % (
        n_assets, res["n_points"], "%.3f" % res["corr"] if "corr" in res else None, "%.3f" % res["slope"] if "slope" in res else None))
    return res


# ------------------------------------------------------------------------------------------------------ главный проход
def run(a, log):
    out_root = a.out
    xdl9 = a.xdl9
    t0 = time.time()
    inst_path = os.path.join(xdl9, "inst_gate", "gate.csv")
    rows = list(csv.DictReader(open(inst_path, encoding="utf-8", newline="")))
    log("manifest: контрактов в instruments %d; формирование сутки %d..%d (окно >= %d не читается)" % (len(rows), D0, D1, D1 + 1))
    with multiprocessing.Pool(a.procs) as pool:
        res = pool.map(contract_task, [(out_root, xdl9, r) for r in rows], chunksize=8)
    recs = {r["symbol"]: z for r, z in zip(rows, res)}
    log("manifest: контракты разобраны за %.0f с" % (time.time() - t0))
    details = dict(formation=dict(d0=D0, d1=D1, start_ms=FORM_START_MS, end_ms_exclusive=FORM_END_MS, minutes=N_FORM), checks={}, contracts_n=len(rows))
    fails = {}

    # ----- rows
    n_ct = len(rows)
    miss = {k: [s for s, z in recs.items() if not z.get(k)] for k in ("has_candles", "has_archive", "has_funding", "has_fund_archive", "has_panel")}
    mism = [s for s, z in recs.items() if z.get("has_candles") and z.get("has_archive") and not z.get("same_minutes")]
    bad_val = [s for s, z in recs.items() if z.get("same_minutes") and max(z.get(k, 0) for k in ("max_rel_o", "max_rel_h", "max_rel_l", "max_rel_c", "max_rel_v")) > TOL]
    bad_panel = [s for s, z in recs.items() if z.get("has_panel") and (not z.get("panel_valid_eq") or z.get("panel_max_rel_c", 0) > TOL or z.get("panel_max_rel_o", 0) > TOL)]
    n_file = sum(z.get("n_file", 0) for z in recs.values())
    n_raw = sum(z.get("n_raw", 0) for z in recs.values())
    n_pos = sum(z.get("n_pos", 0) for z in recs.values())
    n_pvalid = sum(z.get("panel_valid", 0) for z in recs.values())
    rows_ok = not any(miss.values()) and not mism and not bad_val and not bad_panel and n_file == n_raw and n_pos == n_pvalid
    details["checks"]["rows"] = dict(contracts=n_ct, rows_file_formation=n_file, rows_archive_formation=n_raw, rows_v_pos_c_pos=n_pos, panel_valid_minutes=n_pvalid,
                                     missing={k: v[:20] for k, v in miss.items()}, minutes_differ=mism[:20], value_mismatch=bad_val[:20], panel_mismatch=bad_panel[:20],
                                     v_zero_rows=sum(z.get("n_v0", 0) for z in recs.values()), ok=rows_ok)
    if not rows_ok:
        fails["rows"] = "missing=%s minutes_differ=%d value_mismatch=%d panel_mismatch=%d rows %d/%d pos %d/%d" % (
            {k: len(v) for k, v in miss.items()}, len(mism), len(bad_val), len(bad_panel), n_file, n_raw, n_pos, n_pvalid)
    log("rows: контрактов %d, строк файла/архива за формирование %d/%d, строк v>0&c>0 %d, валидных минут панелей %d -> %s" % (n_ct, n_file, n_raw, n_pos, n_pvalid, "ok" if rows_ok else "FAIL"))

    # ----- выравнивание + кросс-проверка оборота
    keyed = {r["symbol"]: r for r in csv.DictReader(open(os.path.join(xdl9, "inst_gate", "gate_asset_key.csv"), encoding="utf-8", newline=""))}
    cross = alignment_and_units_cross(xdl9, out_root, rows, recs, log)

    # ----- gaps
    unordered = [s for s, z in recs.items() if z.get("has_candles") and (not z.get("ordered", True) or not z.get("raw_ordered", True))]
    offgrid = sum(z.get("off_grid", 0) for z in recs.values())
    hdr_bad = [s for s, z in recs.items() if z.get("has_panel") and (z.get("panel_first", 0) < D0 * 1440 or (z.get("first_minute_row") and z.get("panel_first") != D0 * 1440))]
    gaps_ok = not unordered and offgrid == 0 and not hdr_bad and cross["align_bad"] == 0 and cross["align_measurable"] > 0
    details["checks"]["gaps"] = dict(unordered_or_duplicates=unordered[:20], off_grid_rows=offgrid, panel_header_bad=hdr_bad[:20], align_pairs=len(cross["align"]),
                                     align_measurable=cross["align_measurable"], align_peak_not_lag0=cross["align_bad"], n_ret_min=N_RET_MIN,
                                     align_detail=cross["align"], ok=gaps_ok)
    if not gaps_ok:
        fails["gaps"] = "unordered=%d off_grid=%d panel_header_bad=%d align_peak_not_lag0=%d из %d" % (len(unordered), offgrid, len(hdr_bad), cross["align_bad"], cross["align_measurable"])
    log("gaps: -> %s" % ("ok" if gaps_ok else "FAIL " + fails["gaps"]))

    # ----- units
    quantos = [z["quanto"] for z in recs.values()]
    pow10 = all(q > 0 for q in quantos)                       # quanto положительны (не обязательно степени десяти: ACH_USDT = 11)
    max_turn_rel = max((z.get("turn_rel", 0.0) for z in recs.values()), default=0.0)
    tf_all = sum(z.get("turn_file", 0.0) for z in recs.values())
    tr_all = sum(z.get("turn_raw", 0.0) for z in recs.values())
    mult_on_file_v = tf_all / tr_all if tr_all > 0 else float("nan")
    units_ok = pow10 and max_turn_rel <= TOL and cross["cross_ok"] and abs(mult_on_file_v - 1.0) <= TOL
    details["checks"]["units"] = dict(quanto_distinct=sorted(set(quantos)), quanto_all_positive=pow10, turnover_rel_max_over_contracts=max_turn_rel,
                                      turnover_file_total=tf_all, turnover_raw_archive_total=tr_all, mult_on_file_v=mult_on_file_v,
                                      cross_exchange_top=cross["ratios"], cross_median_log10=cross["ratios_median_log10"], cross_top_ok=cross["cross_top_ok"],
                                      cross_all_assets=cross["all_assets"], cross_all_median_log10=cross["all_median_log10"], buckets_by_quanto=cross["buckets_by_quanto"],
                                      bucket_bad=cross["bucket_bad"], outliers_abs_dev_ge_1=cross["outliers_abs_dev_ge_1"], cross_ok=cross["cross_ok"], ok=units_ok)
    if not units_ok:
        fails["units"] = "quanto_positive=%s turn_rel_max=%.3g mult_on_file_v=%.9g cross_ok=%s" % (pow10, max_turn_rel, mult_on_file_v, cross["cross_ok"])
    log("units: quanto положительны %s, макс. относительное расхождение оборота по контрактам %.3g, множитель на v файла %.9g, кросс-биржа %s -> %s" % (
        pow10, max_turn_rel, mult_on_file_v, cross["cross_ok"], "ok" if units_ok else "FAIL"))

    # ----- open_semantics
    shares = np.array([z["open_n_eq"] / z["open_n_both"] for z in recs.values() if z.get("open_n_both", 0) >= N_RET_MIN])
    n_meas = int(len(shares))
    n_need = sum(1 for z in recs.values() if z.get("open_n_both", 0) > 0)
    med = float(np.median(shares)) if n_meas else float("nan")
    label = "chain" if med >= CHAIN_TH else "first_trade"
    os_ok = n_meas > 0
    details["checks"]["open_semantics"] = dict(contracts_measured=n_meas, contracts_with_some_pairs=n_need, share_median=med, share_q10=float(np.quantile(shares, 0.1)) if n_meas else None,
                                               share_q90=float(np.quantile(shares, 0.9)) if n_meas else None, share_min=float(shares.min()) if n_meas else None,
                                               share_max=float(shares.max()) if n_meas else None, label=label, chain_threshold=CHAIN_TH,
                                               n_pairs_total=sum(z.get("open_n_both", 0) for z in recs.values()), ok=os_ok)
    log("open_semantics: контрактов %d, доля open[t]=close[t-1] медиана %.4f (q10 %.4f, q90 %.4f), метка %s -> %s" % (
        n_meas, med, details["checks"]["open_semantics"]["share_q10"] or float("nan"), details["checks"]["open_semantics"]["share_q90"] or float("nan"), label, "ok" if os_ok else "FAIL"))

    # ----- funding
    fund_cnt_diff = [s for s, z in recs.items() if z.get("fund_n_file") != z.get("fund_n_raw") or z.get("fund_n_file") is None]
    fund_rate_diff = [s for s, z in recs.items() if z.get("fund_n_file") is not None and z.get("fund_n_file") == z.get("fund_n_raw") and not z.get("fund_rate_eq", False)]
    fund_min_diff = [s for s, z in recs.items() if z.get("fund_n_file") is not None and z.get("fund_n_file") == z.get("fund_n_raw") and not z.get("fund_min_eq", False)]
    fvb = funding_vs_binance(xdl9, out_root, keyed, rows, log)
    sign_ok = fvb.get("corr", -1) > 0 and fvb.get("slope", -1) > 0 and 0.1 <= fvb.get("slope", 0) <= 10
    nfund = sum(z.get("fund_n_file", 0) for z in recs.values())
    fund_ok = not fund_cnt_diff and not fund_rate_diff and not fund_min_diff and sign_ok and not miss["has_funding"]
    details["checks"]["funding"] = dict(contracts=n_ct, payments_file_formation=nfund, payments_archive_formation=sum(z.get("fund_n_raw", 0) for z in recs.values()),
                                        count_mismatch=fund_cnt_diff[:20], rate_mismatch=fund_rate_diff[:20], minute_mismatch=fund_min_diff[:20],
                                        exact_time_matches=sum(z.get("fund_exact_t", 0) for z in recs.values()),
                                        max_time_drift_ms=max((z.get("fund_max_dt_ms", 0) for z in recs.values()), default=0), vs_binance=fvb, sign_and_units_ok=sign_ok, ok=fund_ok)
    if not fund_ok:
        fails["funding"] = "count_mismatch=%d rate_mismatch=%d minute_mismatch=%d sign_ok=%s" % (len(fund_cnt_diff), len(fund_rate_diff), len(fund_min_diff), sign_ok)
    log("funding: выплат формирования в файлах %d, расхождений число/ставка/минута %d/%d/%d, знак-единицы %s -> %s" % (
        nfund, len(fund_cnt_diff), len(fund_rate_diff), len(fund_min_diff), sign_ok, "ok" if fund_ok else "FAIL"))

    # ----- classes
    cc = collections.Counter(r["class"] for r in rows)
    c2 = collections.Counter(CLS_C2.get(r["class"], "вне") for r in rows)
    badc = [r["symbol"] for r in rows if r["class"] not in CLS_C2]
    srcbad = []
    for r in rows:
        src = r["class_src"]
        want = {"crypto": ("default(contract_type empty)", "contract_type=forex+base=stablecoin"), "equity": ("contract_type=stocks",), "index": ("contract_type=indices",),
                "metal": ("contract_type=metals",), "energy": ("contract_type=commodities+ticker",), "fx": ("contract_type=forex",)}.get(r["class"], ())
        if src not in want:
            srcbad.append((r["symbol"], r["class"], src))
    classes_ok = not badc and not srcbad and len({r["symbol"] for r in rows}) == len(rows)
    details["checks"]["classes"] = dict(by_gate_class=dict(cc), by_c2_class=dict(c2), outside_c2=badc[:20], class_src_inconsistent=srcbad[:20],
                                        metal_energy_fx_index_symbols={k: sorted(r["symbol"] for r in rows if r["class"] == k) for k in ("index", "metal", "energy", "fx")}, ok=classes_ok)
    if not classes_ok:
        fails["classes"] = "outside_c2=%d class_src_inconsistent=%d" % (len(badc), len(srcbad))
    log("classes: %s; классы C2 %s -> %s" % (dict(cc), dict(c2), "ok" if classes_ok else "FAIL"))

    oks = {"rows": rows_ok, "gaps": gaps_ok, "units": units_ok, "open_semantics": os_ok, "funding": fund_ok, "classes": classes_ok}
    details["ok"] = oks
    details["seconds"] = round(time.time() - t0, 1)
    # ----- манифест
    old = os.path.join(xdl9, "gate_ready")
    sha_list = os.path.join(xdl9, "gx", "sha256-gate.txt")
    lines = ["# gate_ready: манифест готовности Gate по П-38 §3.4 (проверки только на формировании, сутки %d..%d; окно не читалось)" % (D0, D1),
             "# строка имя=ok пишется только после реальной проверки; невалидный манифест = любая строка не ok"]
    for k in CHECKS:
        lines.append("%s=ok" % k if oks[k] else "%s=FAIL: %s" % (k, fails.get(k, "")))
    inst_sha = sha256_file(inst_path)
    pm = sha256_file(sha_list) if os.path.isfile(sha_list) else ""
    lines += ["symbols=%d" % n_ct,
              "formation_days=%d..%d" % (D0, D1),
              "manifest_checks_window_read=no",
              "panels_dir=%s" % os.path.join(xdl9, "panels", "gate"),
              "funding_dir=%s" % os.path.join(out_root, "gate", "funding"),
              "raw_root=%s" % out_root,
              "units_turnover_mult_on_file_v=%.9g" % mult_on_file_v,
              "units_v_semantics=base_units (контракты × quanto_multiplier, так пишет gate.py)",
              "open_semantics_label=%s" % label,
              "open_semantics_share_median=%.6f" % med,
              "classes_counts=%s" % ",".join("%s:%d" % kv for kv in sorted(cc.items())),
              "instruments_sha256=%s" % inst_sha,
              "panels_manifest_sha256=%s" % pm,
              "panels_manifest_note=sha256 файла gx/sha256-gate.txt (строки sha256sum панелей); панели в G1 не пересчитывались и целиком не читались",
              "gate_py_sha256=%s" % sha256_lf(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gate.py")),
              "gate_manifest_py_sha256=%s" % sha256_lf(os.path.abspath(__file__)),
              "caveats=15 контрактов enable_decimal (v целое число контрактов, минуты с одними дробными сделками имеют v=0), 15 пред-рыночных контрактов (is_pre_market), Gate в F8 не входит",
              "generated_utc=%s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]
    text = "\n".join(lines) + "\n"
    det_path = os.path.join(xdl9, "gx", "gate_manifest_checks.json")
    os.makedirs(os.path.dirname(det_path), exist_ok=True)
    with open(det_path, "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=1, sort_keys=True, default=float)
    log("manifest: подробности %s" % det_path)
    if not a.write_manifest:
        log("manifest: --write-manifest не задан, файл gate_ready не записан; черновик:\n" + text)
        return all(oks.values())
    if os.path.isfile(old) and not os.path.isfile(old + ".old"):
        os.replace(old, old + ".old")
    elif os.path.isfile(old):
        log("manifest: gate_ready.old уже есть, текущий gate_ready заменяется без переименования")
    with open(old, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("manifest: записан %s (%s)" % (old, "валиден: все шесть проверок ok" if all(oks.values()) else "НЕВАЛИДЕН: " + ", ".join(k for k in CHECKS if not oks[k])))
    return all(oks.values())
