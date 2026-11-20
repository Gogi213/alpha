#!/usr/bin/env python3
"""П-42 §13.38 (TK-642): правила владельца `pk`, `pb` / `ap`, свежий уровень `fh` / `fm` — по одному выключить на базах заморозки TK-626, h775, 136 сут. Запуск на srv юнитом (`tk642_pnl.sh`):
  python3 tk642_run.py cells    — движок `breakout tick` замороженным бинарём: 3 базы (контроль) + 11 клеток × 3 базы, окна wa / wb / wc (выход /data/p42-tk642/cells/<окно>/<вариант>/)
  python3 tk642_run.py gate     — G1/G2: бинарь = замороженному; `L_base` / `S_base` прогона = готовым базам h775 TK-625 байт в байт (trades / daily / coinday / entryrej × 3 окна)
  python3 tk642_run.py counts   — без P&L: число сделок клеток и баз, доля сделок клетки среди сделок базы (ступень до PASS)
  python3 tk642_run.py tables   — P&L (только после PASS критика): Σ $, $/сделку, n, Sharpe, месяцы, парная ΔΣ по суткам (бутстреп, блок 4), p двусторонний, Холм m = 11 на базу, MDE, шумовая планка
"""
from __future__ import annotations

import argparse
import filecmp
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402

ROOT = "/data/p42-tk642"
FROZEN_BIN = "/data/p42-tk616/alpha-mk.396989e9"
FROZEN_SHA = "470046d924392f0eab60ee730c43eb943c17913463857458e93b702127ac7fe0"
FREEZE = os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk626-frozen.json")
BASES = ("L_base", "S_base", "S_mk1")
SIDE = {"L_base": "L", "S_base": "S", "S_mk1": "S"}
# клетки §13.38 п. 3: правка ключей базы (ключ → значение); порядок — порядок отчёта
CELLS = {
    "pk0": {"pk": "0"},                                  # прокол не убивает уровень (§13.11 выкл.)
    "pb0": {"pb": "0"},                                  # минутка со сделкой на уровне не убивает (§13.13 `pb` выкл.)
    "ap1": {"ap": "1"},                                  # + подход с правильной стороны поверх `pb=1`
    "pb0ap1": {"pb": "0", "ap": "1"},                    # `ap` вместо `pb`
    "F0": {"fh": "0", "fm": "0"},                        # свежий уровень выключен целиком
    "fa0": {"fh": "0"},                                  # только проторговка (б)
    "fb0": {"fm": "0"},                                  # только касание ≤ 4 ч (а)
    "fh2": {"fh": "2"},
    "fh8": {"fh": "8"},
    "fh24": {"fh": "24"},
    "ALL0": {"pk": "0", "pb": "0", "fh": "0", "fm": "0"},  # все правила владельца выключены
}
M_HOLM = len(CELLS)
ALPHA = 0.05
FILES = ("trades", "daily", "coinday", "entryrej")
say = r4.say


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def frozen_keys():
    fz = json.load(open(FREEZE, encoding="utf-8"))
    if fz["binary"]["sha256"] != FROZEN_SHA:
        raise st.GuardError("заморозка: sha256 бинаря ≠ записанному")
    return {b: fz["cells"][b]["keys"] for b in BASES}


def patch(keys, kv):
    toks = [t.split("=", 1) for t in keys.split()]
    d = dict(toks)
    order = [k for k, _ in toks]
    for k, v in kv.items():
        if k not in d:
            order.append(k)
        d[k] = v
    return " ".join("%s=%s" % (k, d[k]) for k in order)


def variants():
    fk = frozen_keys()
    out = [(b, fk[b]) for b in BASES]
    for b in BASES:
        for c, kv in CELLS.items():
            out.append(("%s__%s" % (b, c), patch(fk[b], kv)))
    return out


def wdir_cells(wname, vid):
    return os.path.join(ROOT, "cells", wname, vid)


def cmd_cells(a):
    u = r5.Uni("h775")
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        raise st.GuardError("бинарь ≠ замороженному")
    vs = variants()
    for w in st.TRADE_WINDOWS:
        st.check_dates(w.t0, w.hi)
        out = os.path.join(ROOT, "cells", w.name)
        if os.path.exists(out + "/DONE"):
            continue
        os.makedirs(out, exist_ok=True)
        vf = out + ".variants.txt"
        with open(vf, "w", encoding="utf-8") as f:
            f.write("".join("%s %s\n" % v for v in vs))
        d = u.wdir(w.name)
        cmd = [FROZEN_BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf,
               "--symbols", open(u.coins_file, encoding="utf-8").read().strip(), "--out", out, "--threads", str(a.threads)] + r5.TICK_STORE
        r4.run(cmd, {"P42_T0": w.t0}, q("log", "cells-%s.log" % w.name))
        open(out + "/DONE", "w").close()
    say("cells: %d вариантов × 3 окна готово" % len(vs))


def cmd_gate(a):
    u = r5.Uni("h775")
    bad = []
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        bad.append("бинарь ≠ замороженному")
    n = 0
    for w in st.TRADE_WINDOWS:
        for b in ("L_base", "S_base"):
            eng = r5.ENG[SIDE[b]]
            for f in FILES:
                x, y = os.path.join(wdir_cells(w.name, b), "%s-%s.csv" % (eng, f)), os.path.join(u.base(w.name, SIDE[b]), "%s-%s.csv" % (eng, f))
                n += 1
                if not filecmp.cmp(x, y, shallow=False):
                    bad.append("%s/%s/%s-%s ≠ база TK-625" % (w.name, b, eng, f))
    r4.write_json(q("gates", "g2.json"), dict(files=n, bad=bad))
    say("G1/G2", "FAIL " + "; ".join(bad[:6]) if bad else "ok: бинарь = замороженному; %d файлов баз байт в байт = h775 TK-625" % n)
    if bad:
        sys.exit(1)


def load(vid):
    """Ряд сделок варианта на 01.07–13.11 (склейка wa / wb / wc по зачётным суткам, охраны `st.splice`)."""
    u = r5.Uni("h775")
    eng = r5.ENG[SIDE[vid.split("__")[0]]]
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d = wdir_cells(w.name, vid)
        tables[w.name] = st.read_trades_csv(os.path.join(d, eng + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, eng + "-daily.csv"))
    return st.splice(tables, u.coins(), daily=daily)


def keyset(ser):
    return set(zip(ser.sym_name, [int(x) for x in ser.key_ms]))


def cmd_counts(a):
    """Без P&L: n сделок и пересечение с базой по ключу (монета, время сигнала)."""
    res = {}
    for b in BASES:
        bs = load(b)
        bk = keyset(bs)
        res[b] = dict(n=len(bs), cells={})
        for c in CELLS:
            s = load("%s__%s" % (b, c))
            k = keyset(s)
            inter = len(k & bk)
            res[b]["cells"][c] = dict(n=len(s), in_base=inter, only_cell=len(k) - inter, only_base=len(bk) - inter,
                                      identical=bool(k == bk))
    r4.write_json(q("res", "counts.json"), res)
    for b, r in res.items():
        say("%s n=%d | " % (b, r["n"]) + " ".join("%s:%d(+%d/−%d)" % (c, v["n"], v["only_cell"], v["only_base"]) for c, v in r["cells"].items()))


# ---- P&L (после PASS) ------------------------------------------------------------------------------------------------------

def zq(p):
    """Квантиль N(0,1) (без scipy): бисекция по erf."""
    lo, hi = -10.0, 10.0
    for _ in range(200):
        m = (lo + hi) / 2
        if 0.5 * (1 + math.erf(m / math.sqrt(2))) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


Z_HOLM = zq(1 - ALPHA / (2 * M_HOLM))      # двусторонний, первая ступень Холма
Z_1 = zq(1 - ALPHA / 2)
Z_POW = zq(0.80)


def sharpe_year(Sd):
    s = float(np.std(Sd, ddof=1))
    return float(np.mean(Sd) / s * math.sqrt(365)) if s > 0 else float("nan")


def summary(ser):
    Sd = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
    mon = st.MONTH_OF_DAY[ser.day] if len(ser) else np.zeros(0, dtype=np.int64)
    return dict(n=int(len(ser)), sum=float(ser.val.sum()), mean=float(ser.val.mean()) if len(ser) else float("nan"), sharpe=sharpe_year(Sd),
                months=[(int((mon == k).sum()), float(ser.val[mon == k].sum())) for k in range(len(st.MONTH_LEN))])


def window_sums(Sd):
    out = []
    for w in st.TRADE_WINDOWS:
        lo, hi = (w.lod - st.D0).days, (w.hid - st.D0).days
        out.append(float(Sd[lo:hi + 1].sum()))
    return out


def robust(cs, bs, dsum):
    """ΔΣ без двух суток с наибольшим вкладом того же знака и без монеты с наибольшим вкладом того же знака (знак должен сохраниться)."""
    Dd = np.bincount(cs.day, weights=cs.val, minlength=st.NDAYS) - np.bincount(bs.day, weights=bs.val, minlength=st.NDAYS)
    sg = 1.0 if dsum >= 0 else -1.0
    top_days = np.sort(sg * Dd)[::-1][:2]
    no_days = dsum - sg * float(top_days[top_days > 0].sum())
    coins = {}
    for s, v in zip(cs.sym_name, cs.val):
        coins[s] = coins.get(s, 0.0) + float(v)
    for s, v in zip(bs.sym_name, bs.val):
        coins[s] = coins.get(s, 0.0) - float(v)
    top_coin = max(coins.items(), key=lambda kv: sg * kv[1]) if coins else ("", 0.0)
    no_coin = dsum - (top_coin[1] if sg * top_coin[1] > 0 else 0.0)
    return dict(no_top2_days=no_days, top_coin=top_coin[0], top_coin_delta=top_coin[1], no_top_coin=no_coin,
                holds=bool(sg * no_days > 0 and sg * no_coin > 0))


def delta(cs, bs):
    Sc, Sr = (np.bincount(s.day, weights=s.val, minlength=st.NDAYS) for s in (cs, bs))
    Nc, Nr = (np.bincount(s.day, minlength=st.NDAYS).astype(float) for s in (cs, bs))
    idx = st.block_idx(st.NDAYS, np.random.default_rng(st.seed_of(Sc, Nc, Sr, Nr)))
    D = Sc - Sr
    dsum = float(D.sum())
    bsum = D[idx].sum(axis=1)
    se = float(bsum.std(ddof=1))
    cen = bsum - bsum.mean()                    # распределение при H0 (сдвиг к 0)
    p2 = (1 + int((np.abs(cen) >= abs(dsum)).sum())) / (1 + len(cen))
    lo, hi = np.percentile(bsum, [2.5, 97.5])
    bNc, bNr = Nc[idx].sum(axis=1), Nr[idx].sum(axis=1)
    bpt = Sc[idx].sum(axis=1) / bNc - Sr[idx].sum(axis=1) / bNr
    dpt = float(Sc.sum() / Nc.sum() - Sr.sum() / Nr.sum())
    return dict(dsum=dsum, se_sum=se, lo95=float(lo), hi95=float(hi), p2=float(p2), z=dsum / se if se > 0 else float("nan"),
                dmean=dpt, se_mean=float(np.nanstd(bpt, ddof=1)),
                mde_sum_holm=(Z_HOLM + Z_POW) * se, mde_sum_1=(Z_1 + Z_POW) * se,
                mde_mean_holm=(Z_HOLM + Z_POW) * float(np.nanstd(bpt, ddof=1)),
                windows=[c - r for c, r in zip(window_sums(Sc), window_sums(Sr))],
                months=[float(D[st.MONTH_OF_DAY == k].sum()) for k in range(len(st.MONTH_LEN))])


def emax_abs_z(n, reps=200_000, seed=642):
    z = np.abs(np.random.default_rng(seed).standard_normal((reps, n)))
    return float(z.max(axis=1).mean()), float(np.percentile(z.max(axis=1), 95))


def holm(ps, alpha=ALPHA):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    rej, m = [False] * len(ps), len(ps)
    for r, i in enumerate(order):
        if ps[i] <= alpha / (m - r):
            rej[i] = True
        else:
            break
    return rej


def verdict(d, rej, rb):
    if rej and d["dsum"] > 0 and all(x > 0 for x in d["windows"]) and rb["holds"]:
        return "снятие правила лучше базы (найдено на этих данных)"
    if rej and d["dsum"] < 0 and all(x < 0 for x in d["windows"]) and rb["holds"]:
        return "правило полезно (снятие хуже базы)"
    if rej:
        return "различие значимо, но не устойчиво (окна / сутки / монета)"
    return "не различимо: |ΔΣ| < MDE %.0f $ не исключено" % d["mde_sum_holm"]


def cmd_tables(a):
    res = dict(z_holm=Z_HOLM, z_pow=Z_POW, m_holm=M_HOLM, noise={}, bases={})
    for n in (M_HOLM, M_HOLM * len(BASES)):
        res["noise"][str(n)] = dict(zip(("emax_abs_z", "p95_max_abs_z"), emax_abs_z(n)))
    for b in BASES:
        bs = load(b)
        r = dict(base=summary(bs), cells={})
        rows = []
        for c in CELLS:
            cs = load("%s__%s" % (b, c))
            d = delta(cs, bs)
            rows.append((c, cs, d))
        rej = holm([d["p2"] for _, _, d in rows])
        for (c, cs, d), rj in zip(rows, rej):
            rb = robust(cs, bs, d["dsum"])
            r["cells"][c] = dict(summary(cs), delta=d, holm_reject=bool(rj), robust=rb, verdict=verdict(d, rj, rb))
        res["bases"][b] = r
    r4.write_json(q("res", "tables.json"), res)
    for b, r in res["bases"].items():
        bb = r["base"]
        say("== %s: n %d, Σ %+.1f $, %+.3f $/сд., Sharpe %.2f" % (b, bb["n"], bb["sum"], bb["mean"], bb["sharpe"]))
        for c, v in r["cells"].items():
            d = v["delta"]
            say("  %-7s n %5d Σ %+8.1f ΔΣ %+8.1f [%+.0f; %+.0f] p2 %.4f z %+.2f MDE %.0f Δ$/сд %+.3f Sh %.2f | %s" % (
                c, v["n"], v["sum"], d["dsum"], d["lo95"], d["hi95"], d["p2"], d["z"], d["mde_sum_holm"], d["dmean"], v["sharpe"], v["verdict"]))


COMMANDS = {"cells": cmd_cells, "gate": cmd_gate, "counts": cmd_counts, "tables": cmd_tables}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
