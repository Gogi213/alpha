#!/usr/bin/env python3
"""П-42 §13.68 (TK-668), линия E4 / 45 — новые источники уровней (ключ движка `lx`: 1 `D` вчерашний день, 2 `W` прошлая неделя, 3 `P` профиль объёма). Подкоманды:
  gbyte   — G-bin + G-byte: базы `L_base` / `S_base` новым бинарём (`lx = 0`) × окна wa / wb / wc = готовые базы h775 байт в байт (файлы сравниваются, P&L не читается) → res/gbyte-e4.json
  counts  — счёт источников без P&L (`P42_SIGSX_ONLY`: только сигналы и дампы уровней): родилось / живых на сигнале / сигналов / ключей кандидатов; независимый пересчёт уровней
            из кэша минуток на Python (D, W, P; цена, `born`, `end`, `first_bar`) против дампа движка (G-lvl-E4) → res/counts68-e4.json
  cells   — полный прогон 3 источника × 2 стороны × 3 окна (только после PASS критика на §13.68.3 и G-byte; сигналы прогона = сигналам счёта побайтно, G-sig)
  lines   — P&L линии E4: по источникам D / W / P × стороны — ряд суток, (l1)–(l5) (Холм семейства линий m_L = 7: E8 + 6 клеток E4), месяцы, R:R
Доллары — ставка 500 $, годовой Sharpe по суточному ряду; без бп, без медиан. Файлы сделок / суточные прогона `cells` открывает только `lines`; счёт и ворота их не читают."""
from __future__ import annotations

import argparse
import datetime
import filecmp
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk649_run as t49  # noqa: E402  (tick с BIN, base_line, meta_equal)
import tk668_feat as ft  # noqa: E402

ROOT = "/data/p42-tk668"
BIN = os.path.join(ROOT, "bin", "alpha-lx")
SIDES = st.SIDES
ENG, VID, BASE = r5.ENG, r5.VID, r5.BASE
SRC = {1: "D", 2: "W", 3: "P"}
SIGSX = "14:73"                       # как `tk625_run.cmd_eng` для wa / wb / wc
HOUR = 60
say, write_json, sha256 = r4.say, r4.write_json, r4.sha256
GuardError = st.GuardError


def qq(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def lw_of(w):
    """Дни от T0 окна до первого понедельника (`lw` ключа движка): понедельник = 0."""
    return (7 - datetime.date.fromisoformat(w.t0).weekday()) % 7


def vid(side, lx):
    return "%s_lx%d" % (side, lx)


def e4_line(u, side, lx, w):
    return "%s%s lx=%d tf=60 lw=%d" % (BASE[side], u.extra, lx, lw_of(w))


def e4_variants(u, w):
    return [(vid(s, lx), e4_line(u, s, lx, w)) for lx in SRC for s in SIDES]


def tick(u, w, variants, out, env, log):
    t49.BIN = BIN
    st.check_dates(w.t0, w.hi)
    t49.tick(r4, r5, u, w, variants, out, env, log)


# ---- G-byte ------------------------------------------------------------------------------------------------------------------------

def cmd_gbyte(a):
    u = r5.Uni("h775")
    sha = sha256(BIN)
    res, bad = {"bin_sha256": sha, "windows": {}}, []
    for w in st.TRADE_WINDOWS:
        out = qq("e4", "gbyte", w.name)
        if not os.path.exists(out + "/DONE"):
            tick(u, w, [(VID[s], t49.base_line(r5, u, s)) for s in SIDES], out, {"P42_SIGSX": SIGSX}, qq("log", "e4-gbyte-%s.log" % w.name))
            open(out + "/DONE", "w").close()
        ref = os.path.dirname(u.base(w.name, "L"))
        rw = {}
        for s in SIDES:
            nd, od = os.path.join(out, VID[s]), os.path.join(ref, VID[s])
            files = sorted(set(os.listdir(nd)) | set(os.listdir(od)))
            diff = []
            for fn in files:
                pn, po = os.path.join(nd, fn), os.path.join(od, fn)
                if not (os.path.exists(pn) and os.path.exists(po)):
                    diff.append(fn + ": нет в одном из каталогов")
                elif fn == "meta.json":
                    ok, why = t49.meta_equal(pn, po)
                    rw.setdefault(VID[s] + "/meta", why)
                    if not ok:
                        diff.append("meta.json: " + why)
                elif not filecmp.cmp(pn, po, shallow=False):
                    diff.append(fn + ": байты различаются")
            rw[VID[s]] = dict(files=len(files), diff=diff)
            bad += ["%s %s: %s" % (w.name, VID[s], x) for x in diff]
        sn, so = os.path.join(out, "summary.csv"), os.path.join(ref, "summary.csv")
        rw["summary.csv"] = os.path.exists(sn) and os.path.exists(so) and filecmp.cmp(sn, so, shallow=False)
        if not rw["summary.csv"]:
            bad.append("%s summary.csv: байты различаются" % w.name)
        res["windows"][w.name] = rw
    res["ok"], res["bad"] = not bad, bad
    write_json(qq("res", "gbyte-e4.json"), res)
    say("G-byte E4 (lx = 0 = базе): %s; sha256 %s" % ("ok" if not bad else "ОШИБКА " + "; ".join(bad), sha))
    if bad:
        sys.exit(1)


# ---- независимый пересчёт уровней ---------------------------------------------------------------------------------------------------

def hourly(a):
    """Часовые бары из минуток: ok — есть хотя бы одна минутка с данными (o ≠ 0); h / l — по минуткам с данными; c — закрытие последней минутки с данными."""
    nb = len(a["o"]) // HOUR
    sh = lambda x: x[: nb * HOUR].reshape(nb, HOUR)  # noqa: E731
    has = sh(a["o"]) != 0
    big = np.int64(4 * 10 ** 18)
    h = np.where(has, sh(a["h"]), -big).max(1)
    l = np.where(has, sh(a["l"]), big).min(1)
    last = HOUR - 1 - np.argmax(has[:, ::-1], axis=1)
    c = sh(a["c"])[np.arange(nb), last]
    return dict(ok=has.any(1), h=h, l=l, c=c)


def src_levels(a, s, lx, lw):
    """Уровни источника `lx` монеты: [(born, end, first_bar, цена)] — тот же порядок, что у `levels.rs::build_source_levels`, но независимый код (Python, numpy)."""
    b = hourly(a)
    nb = len(b["ok"])
    hi = b["h"] if s > 0 else -b["l"]
    cl = s * b["c"]
    step, a0 = (168, lw * 24) if lx == 2 else (24, 0)
    out = []
    while a0 + step < nb:
        z = a0 + step
        found = poc(a, s, a0, z) if lx == 3 else extreme(hi, b["ok"], a0, z)
        if found is not None:
            price, fb = found
            end_bar = next((i for i in range(z, min(z + step, nb)) if b["ok"][i] and cl[i] > price), None)
            out.append((z * HOUR, (z + step) * HOUR if end_bar is None else (end_bar + 1) * HOUR, int(fb), abs(int(price))))
        a0 += step
    return out


def extreme(hi, ok, a0, z):
    if not ok[a0:z].all():
        return None
    seg = hi[a0:z]
    p = seg.max()
    return int(p), a0 + int(np.argmax(seg == p))


def poc(a, s, a0, z):
    m0, m1 = a0 * HOUR, z * HOUR
    has = a["o"][m0:m1] != 0
    if int(has.sum()) < 1200:
        return None
    mm = np.flatnonzero(has) + m0
    tp = (a["h"][mm] + a["l"][mm] + a["c"][mm]) // 3
    mean = int(tp.sum(dtype=np.int64)) // len(tp)
    w = max(mean // 1000, 1)
    qv = a["qv"][mm]
    bins = tp // w
    ub, inv = np.unique(bins, return_inverse=True)
    tot = np.zeros(len(ub), dtype=np.int64)
    np.add.at(tot, inv, qv)
    g = int(np.argmax(tot))
    if tot[g] <= 0:
        return None
    idx = np.flatnonzero(inv == g)
    k = idx[int(np.argmax(qv[idx]))]
    return s * (int(ub[g]) * w + w // 2), int(mm[k]) // HOUR


def read_dump(path):
    """Дамп уровней движка → {монета: [(born, end, first_bar, цена)]} в порядке файла."""
    out = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.rstrip("\n").split(",")
            segs = p[5].split("|")
            if len(segs) != 1:
                raise GuardError("%s: у уровня источника один сегмент, получено %d" % (path, len(segs)))
            out.setdefault(p[0], []).append((int(p[2]), int(p[3]), int(p[4]), int(segs[0].split(":")[1])))
    return out


def read_sigsx(path):
    keys, lv = set(), set()
    n = 0
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.split(",", 3)
            n += 1
            lv.add((p[0], p[1]))
            keys.add((p[0], int(p[2])))
    return n, len(lv), len(keys)


# ---- счёт без P&L -----------------------------------------------------------------------------------------------------------------

def cmd_counts(a):
    u = r5.Uni("h775")
    coins = u.coins()
    res = dict(bin_sha256=sha256(BIN), sigsx_days=SIGSX, windows={}, check={}, totals={})
    bad = []
    tot = {}
    for w in st.TRADE_WINDOWS:
        out = qq("e4", "counts", w.name)
        if not os.path.exists(out + "/DONE"):
            tick(u, w, e4_variants(u, w), out, {"P42_SIGSX_ONLY": "1", "P42_SIGSX": SIGSX, "P42_LEVELS": "1"}, qq("log", "e4-counts-%s.log" % w.name))
            open(out + "/DONE", "w").close()
        cache = u.wdir(w.name) + "/cache"
        eng = {(s, lx): read_dump(os.path.join(out, vid(s, lx), ENG[s] + "-levels.csv")) for lx in SRC for s in SIDES}
        mism = {k: 0 for k in eng}
        nlv = {k: 0 for k in eng}
        miss_cache = []
        for sym in coins:
            p = os.path.join(cache, sym + ".b42")
            if not os.path.exists(p):
                miss_cache.append(sym)
                continue
            arr = ft.load_b42_full(p)
            for lx in SRC:
                for s in SIDES:
                    mine = src_levels(arr, 1 if s == "L" else -1, lx, lw_of(w))
                    theirs = eng[(s, lx)].get(sym, [])
                    nlv[(s, lx)] += len(mine)
                    if mine != theirs:
                        mism[(s, lx)] += 1
        extra = {k: sorted(set(v) - set(coins)) for k, v in eng.items()}
        rw = {}
        for lx in SRC:
            for s in SIDES:
                k = (s, lx)
                n_sig, n_lv_sig, n_keys = read_sigsx(os.path.join(out, vid(s, lx), ENG[s] + "-sigsx.csv"))
                born = sum(len(v) for v in eng[k].values())
                row = dict(born=born, born_python=nlv[k], mismatch_coins=mism[k], alive_at_signal=n_lv_sig, signals=n_sig, candidate_keys=n_keys)
                rw["%s/%s" % (SRC[lx], s)] = dict(row, lw=lw_of(w))
                t = tot.setdefault("%s/%s" % (SRC[lx], s), dict.fromkeys(row, 0))
                for f in row:
                    t[f] += row[f]
                if mism[k] or born != nlv[k] or extra[k]:
                    bad.append("%s %s %s: уровней движка %d, Python %d, монет с различием %d, лишних монет %s" % (w.name, SRC[lx], s, born, nlv[k], mism[k], extra[k][:3]))
        res["windows"][w.name] = dict(cells=rw, no_cache=miss_cache)
    res["totals"] = tot
    res["bad"], res["ok"] = bad, not bad
    write_json(qq("res", "counts68-e4.json"), res)
    for src in tot:
        say("E4 %s: родилось %d, живых на сигнале %d, сигналов %d, ключей кандидатов %d (по трём окнам)" % (
            src, tot[src]["born"], tot[src]["alive_at_signal"], tot[src]["signals"], tot[src]["candidate_keys"]))
    say("G-lvl-E4 (независимый пересчёт D / W / P = дамп движка):", "ok" if not bad else "ОШИБКА " + "; ".join(bad[:6]))
    if bad:
        sys.exit(1)


# ---- P&L (после PASS критика на §13.68.3) ------------------------------------------------------------------------------------------

def need_pass():
    for f in ("PASS", "PASS-E4"):
        if not os.path.exists(os.path.join(ROOT, f)):
            raise GuardError("нет %s/%s — P&L линии E4 не читается до PASS критика на §13.68.3" % (ROOT, f))
    g = json.load(open(os.path.join(ROOT, "res", "gbyte-e4.json"), encoding="utf-8"))
    if not g["ok"] or g["bin_sha256"] != sha256(BIN):
        raise GuardError("G-byte не пройден или бинарь сменился после G-byte")


def cmd_cells(a):
    need_pass()
    u = r5.Uni("h775")
    bad = []
    for w in st.TRADE_WINDOWS:
        out = qq("e4", "cells", w.name)
        if not os.path.exists(out + "/DONE"):
            tick(u, w, e4_variants(u, w), out, {"P42_SIGSX": SIGSX}, qq("log", "e4-cells-%s.log" % w.name))
            open(out + "/DONE", "w").close()
        for lx in SRC:
            for s in SIDES:
                x = os.path.join(out, vid(s, lx), ENG[s] + "-sigsx.csv")
                y = os.path.join(ROOT, "e4", "counts", w.name, vid(s, lx), ENG[s] + "-sigsx.csv")
                if not (os.path.exists(x) and os.path.exists(y) and filecmp.cmp(x, y, shallow=False)):
                    bad.append("G-sig %s %s %s" % (w.name, SRC[lx], s))
    say("cells E4:", "ОШИБКА " + "; ".join(bad) if bad else "ok: сигналы полного прогона = сигналам счёта побайтно (3 окна × 3 источника × 2 стороны)")
    if bad:
        sys.exit(1)


def cmd_lines(a):
    need_pass()
    import tk638_post as p8
    import tk668_post as post
    u = r5.Uni("h775")
    rows = {}
    for lx in SRC:
        for s in SIDES:
            dirs = {w.name: (os.path.join(ROOT, "e4", "cells", w.name, vid(s, lx)), s) for w in st.TRADE_WINDOWS}
            ser, kinds, pnl = p8.load_cell(u, s, dirs)
            ln = post.line_tests(ser.day, ser.val, list(ser.sym_name))
            rows[(lx, s)] = dict(stats=p8.cell_stats(ser, kinds, pnl), line=ln)
    e8 = json.load(open(os.path.join(ROOT, "res", "e8-68.json"), encoding="utf-8"))["rules"]["main"]["line"]
    keys = list(rows)
    ps = [float(e8["p"])] + [rows[k]["line"]["p"] for k in keys]
    rej = st.holm_reject(ps, post.ALPHA)
    bar = post.noise_bar(len(keys))
    out = dict(m_L=post.M_L, holm_ps=dict(zip(["E8"] + ["%s/%s" % (SRC[k[0]], k[1]) for k in keys], ps)), noise_bar_z=bar[0], noise_bar_p95=bar[1], cells={})
    for i, k in enumerate(keys):
        r = rows[k]
        ln = r["line"]
        ln["l1"] = bool(rej[i + 1] and ln["mean_day"] > 0)
        ln["l5"] = bool(abs(ln["z"]) > bar[0])
        ln["passes_l1_l5"] = bool(ln["l1"] and ln["l2"] and ln["l3"] and ln["l4"] and ln["l5"])
        ln["outcome"] = ("добавляет $ (найдено на этих данных; кандидат только вперёд)" if ln["passes_l1_l5"]
                         else "не работает (среднее ≤ 0, точка)" if not ln["mean_day"] > 0 else "не ясно")
        c = r["stats"]
        nm = "%s/%s" % (SRC[k[0]], k[1])
        out["cells"][nm] = r
        say("   E4 %-4s n %3d Σ %+8.1f $, %+.3f $/сделку, Sharpe %5.2f, месяцы %s | R:R %.2f (выигрыш %+.2f, убыток %+.2f), стопов %d, убыток стопа ср. %+.2f $ (%.2f %% маржи 250 $), макс. %+.2f $" % (
            nm, c["n"], c["sum"], c["mean"], c["sharpe"], [(m[0], round(m[1])) for m in c["months"]], c["rr"]["rr"], c["rr"]["mean_win"], c["rr"]["mean_loss"],
            c["rr"]["stops"]["n"], c["rr"]["stops"]["mean_loss"], c["rr"]["stops"]["mean_loss_pct"], c["rr"]["stops"]["max_loss"]))
        say("        среднее суточное %+.3f $ ± %.3f (z %+.2f), p %.4f; l1 %s l2 %s (мес. %d, +%d, нужно %s) l3 %s (без 2 суток %+.1f, без 2 монет %+.1f) l4 %s l5 %s (планка %.2f) → %s" % (
            ln["mean_day"], ln["se_day"], ln["z"], ln["p"], ln["l1"], ln["l2"], len(ln["informative_months"]), ln["positive_informative"], ln["need"], ln["l3"],
            ln["no_top2_days"], ln["no_top2_coins"], ln["l4"], ln["l5"], bar[0], ln["outcome"]))
    say("Холм линий m_L = %d (E8 p %.4f + 6 клеток E4): %s" % (post.M_L, e8["p"], {n: round(p_, 4) for n, p_ in out["holm_ps"].items()}))
    r4.write_json(qq("res", "e4-lines.json"), _jsonable(out))


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


COMMANDS = {"gbyte": cmd_gbyte, "counts": cmd_counts, "cells": cmd_cells, "lines": cmd_lines}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
