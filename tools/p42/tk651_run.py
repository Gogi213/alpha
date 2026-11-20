#!/usr/bin/env python3
"""П-42 §13.51 (TK-651): 17 «устойчивых по месяцам» килзон TK-622 — правило «не входить в зоне» / «входить только в зоне» на базах заморозки TK-626, h775, 136 сут.
Запуск на srv юнитом (`tk651_pnl.sh`):
  python3 tk651_run.py ftab     — таблицы `--ftab` по кандидатам баз h775 TK-625 (дамп sigsx): столбец на правило (1,0 — пропущен, 0,0 — исключён) + PASS (всё 1,0); число кандидатов в зоне
  python3 tk651_run.py cells    — движок `breakout tick` замороженным бинарём + `--ftab`: окна wa / wb / wc × сторона (выход /data/p42-tk651/cells/<окно>-<сторона>/<вариант>/)
  python3 tk651_run.py gate     — G1 бинарь = заморозке; G2 клетки PASS = базам TK-642 байт в байт; G8 no_key = 0 и dropped = пересчёту по таблице
  python3 tk651_run.py counts   — без P&L: n сделок клеток, пересечение с базой по ключу (монета, время сигнала)
  python3 tk651_run.py tables   — P&L (только после PASS критика): Σ $, $/сделку, n, Sharpe, R:R, стоп, месяцы, окна, парная ΔΣ по суткам, p, Холм m = 17, MDE, шумовая планка
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
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk622_killzones as kz  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk642_run as r42  # noqa: E402

ROOT = "/data/p42-tk651"
BASE642 = "/data/p42-tk642/cells"           # базы L_base / S_base / S_mk1 этого бинаря на h775 (TK-642, G2 там = TK-625)
FROZEN_BIN, FROZEN_SHA = r42.FROZEN_BIN, r42.FROZEN_SHA
ENG = st.ENGINE_SIDE
FV = "0.5"
THREADS = 2
ALPHA = 0.05
BASES = {"L": ("L_base",), "S": ("S_base", "S_mk1")}
DECISION = {"L": "L_base", "S": "S_base"}  # решающая база стороны; S_mk1 — справочно (п. 6)

# 13 правил (сторона, схема TK-622, корзина, действие): skip — не входить в зоне (эффект зоны < 0), only — входить только в зоне (эффект > 0)
RULES = {
    "Z01": ("L", "полчаса, :00 и :30 вместе (мин в полчасе)", "−5…0", "skip"),
    "Z02": ("L", "час (мин до/после начала часа)", "−5…0", "skip"),
    "Z03": ("L", "полчаса, :00 и :30 вместе (мин в полчасе)", "+10…+20", "only"),
    "Z04": ("L", "час суток (UTC)", "00", "skip"),
    "Z05": ("L", "день недели (UTC)", "сб", "only"),
    "Z06": ("L", "фандинг 00/08/16 UTC (мин)", "+10…+30", "skip"),
    "Z07": ("L", "день недели (UTC)", "ср", "skip"),
    "Z08": ("L", "час (мин до/после начала часа)", "+10…+20", "only"),
    "Z09": ("S", "день недели (UTC)", "пт", "skip"),
    "Z10": ("S", "час суток (UTC)", "03", "skip"),
    "Z11": ("S", "полчаса, :00 и :30 вместе (мин в полчасе)", "−10…−5", "only"),
    "Z12": ("S", "час (мин до/после начала часа)", "+20…+40", "only"),
    "Z13": ("S", "час (мин до/после начала часа)", "−10…−5", "skip"),
}
# 17 строк `p42-killzones-2026-11-19/stable.md` в порядке файла: (№, метрика TK-622, правило)
ROWS = [(1, "mo60", "Z09"), (2, "gf", "Z01"), (3, "gf", "Z02"), (4, "brk", "Z03"), (5, "brk", "Z10"), (6, "brk", "Z09"), (7, "gf", "Z04"),
        (8, "gf (Азия 00:00–01:00 = час 00)", "Z04"), (9, "gf", "Z05"), (10, "gf", "Z06"), (11, "brk", "Z07"), (12, "brk", "Z08"),
        (13, "touch", "Z11"), (14, "brk", "Z12"), (15, "mo60", "Z08"), (16, "mo60", "Z03"), (17, "brk", "Z13")]
M_HOLM = len(ROWS)
FILES = ("trades", "daily", "coinday", "entryrej")
say = r4.say


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def zone_mask(abs_ms, scheme, lab):
    hit = [m for s, l, m in kz.buckets(np.asarray(abs_ms, dtype=np.int64)) if s == scheme and l == lab]
    if len(hit) != 1:
        raise st.GuardError("корзина %s / %s: найдено %d" % (scheme, lab, len(hit)))
    return hit[0]


def keep_mask(rid, abs_ms):
    _, scheme, lab, act = RULES[rid]
    z = zone_mask(abs_ms, scheme, lab)
    return ~z if act == "skip" else z


def rules_of(side):
    return [r for r, v in RULES.items() if v[0] == side]


def ftab_path(wname, side):
    return q("ftab", "%s-%s.csv" % (wname, side))


def variants(side):
    fk = r42.frozen_keys()
    out = []
    for b in BASES[side]:
        out.append(("%s__PASS" % b, "%s fa=PASS fo=lo fv=%s" % (fk[b], FV)))
        for rid in rules_of(side):
            out.append(("%s__%s" % (b, rid), "%s fa=%s fo=lo fv=%s" % (fk[b], rid, FV)))
    return out


def sigsx(w, side):
    u = r5.Uni("h775")
    return tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))


def cmd_ftab(a):
    res = {}
    for w in st.TRADE_WINDOWS:
        st.check_dates(w.t0, w.hi)
        for side in st.SIDES:
            sym, rel = sigsx(w, side)
            keys = sorted(set(zip(sym.tolist(), rel.tolist())))
            ks = np.array([k[0] for k in keys], dtype=object)
            km = np.array([k[1] for k in keys], dtype=np.int64)
            absms = km + w.t0_ms
            st.check_abs_ms(absms, "время сигнала кандидата")
            cols = {"PASS": np.ones(len(keys))}
            for rid in rules_of(side):
                cols[rid] = keep_mask(rid, absms).astype(float)
            with open(ftab_path(w.name, side), "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols) + "\n")
                for i in range(len(keys)):
                    f.write("%s,%d,%s\n" % (ks[i], int(km[i]), ",".join("%.1f" % cols[c][i] for c in cols)))
            lo, hi = w.t0_ms + (w.lod - w.t0d).days * 86_400_000, w.t0_ms + ((w.hid - w.t0d).days + 1) * 86_400_000
            sc = (absms >= lo) & (absms < hi)
            res["%s-%s" % (w.name, side)] = dict(keys=len(keys), scored=int(sc.sum()),
                                                  excluded_scored={c: int(((cols[c] < 0.5) & sc).sum()) for c in cols})
            say("ftab %s-%s: кандидатов %d (зачётных %d); исключено (зачётные): %s" % (
                w.name, side, len(keys), int(sc.sum()), " ".join("%s:%d" % (c, v) for c, v in res["%s-%s" % (w.name, side)]["excluded_scored"].items())))
    r4.write_json(q("res", "ftab.json"), res)


def cell_dir(wname, side, vid):
    return os.path.join(ROOT, "cells", "%s-%s" % (wname, side), vid)


def cmd_cells(a):
    u = r5.Uni("h775")
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        raise st.GuardError("бинарь ≠ замороженному")
    for w in st.TRADE_WINDOWS:
        st.check_dates(w.t0, w.hi)
        for side in st.SIDES:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            os.makedirs(out, exist_ok=True)
            vs = variants(side)
            vf = out + ".variants.txt"
            with open(vf, "w", encoding="utf-8") as f:
                f.write("".join("%s %s\n" % v for v in vs))
            d = u.wdir(w.name)
            cmd = [FROZEN_BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf,
                   "--symbols", open(u.coins_file, encoding="utf-8").read().strip(), "--out", out, "--threads", str(a.threads),
                   "--ftab", ftab_path(w.name, side)] + r5.TICK_STORE
            r4.run(cmd, {"P42_T0": w.t0}, q("log", "cells-%s-%s.log" % (w.name, side)))
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(vs), r4.sha256(ftab_path(w.name, side))))
    say("cells готово")


def cmd_gate(a):
    bad, g8 = [], {}
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        bad.append("G1: бинарь ≠ замороженному")
    n = 0
    for w in st.TRADE_WINDOWS:
        for side in st.SIDES:
            eng = ENG[side]
            for b in BASES[side]:
                for f in FILES:
                    x = os.path.join(cell_dir(w.name, side, b + "__PASS"), "%s-%s.csv" % (eng, f))
                    y = os.path.join(BASE642, w.name, b, "%s-%s.csv" % (eng, f))
                    n += 1
                    if not filecmp.cmp(x, y, shallow=False):
                        bad.append("G2: %s/%s__PASS/%s-%s ≠ база TK-642" % (w.name, b, eng, f))
            sym, rel = sigsx(w, side)
            dkeys = list(zip(sym.tolist(), [int(r) for r in rel]))
            for vid, _ in variants(side):
                col = vid.split("__")[1]
                tab = r34.read_table_col(ftab_path(w.name, side), col)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v < 0.5)
                exp_m = sum(1 for k in dkeys if tab.get(k, 1.0) < 0.5)
                m = json.load(open(os.path.join(cell_dir(w.name, side, vid), "meta.json"), encoding="utf-8"))[eng]
                fc = r4.filter_counters(m)
                g8["%s-%s/%s" % (w.name, side, vid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("G8: %s-%s/%s: %s, ожидалось dropped %d / %d, без строки %d" % (w.name, side, vid, fc, exp_u, exp_m, miss))
    r4.write_json(q("gates", "gates.json"), dict(g2_files=n, g8=g8, bad=bad))
    say("G1/G2/G8", "FAIL " + "; ".join(bad[:8]) if bad else "ok: бинарь = заморозке; %d файлов PASS = базам TK-642; G8 ok в %d прогонах" % (n, len(g8)))
    if bad:
        sys.exit(1)


def dirs_of(side, vid):
    if vid.endswith("__BASE"):
        b = vid.split("__")[0]
        return {w.name: (os.path.join(BASE642, w.name, b), side) for w in st.TRADE_WINDOWS}
    return {w.name: (cell_dir(w.name, side, vid), side) for w in st.TRADE_WINDOWS}


def load(side, vid):
    return p8.load_cell(r5.Uni("h775"), side, dirs_of(side, vid))


def keyset(ser):
    return set(zip(ser.sym_name, [int(x) for x in ser.key_ms]))


def cmd_counts(a):
    """Без P&L: n сделок клетки и пересечение с базой по ключу; сделок базы в зоне (по правилу)."""
    res = {}
    for side in st.SIDES:
        for b in BASES[side]:
            bs, _, _ = load(side, b + "__BASE")
            bk = keyset(bs)
            r = dict(n=len(bs), cells={})
            for rid in rules_of(side):
                cs, _, _ = load(side, "%s__%s" % (b, rid))
                k = keyset(cs)
                inter = len(k & bk)
                kept = int(keep_mask(rid, bs.key_ms).sum())
                r["cells"][rid] = dict(n=len(cs), in_base=inter, only_cell=len(k) - inter, only_base=len(bk) - inter, base_kept_by_rule=kept,
                                       base_removed_by_rule=len(bs) - kept)
            res[b] = r
            say("%s n=%d | " % (b, r["n"]) + " ".join("%s:%d(+%d/−%d)" % (c, v["n"], v["only_cell"], v["only_base"]) for c, v in r["cells"].items()))
    r4.write_json(q("res", "counts.json"), res)


# ---- P&L (после PASS) ------------------------------------------------------------------------------------------------------

Z_HOLM = r42.zq(1 - ALPHA / (2 * M_HOLM))
Z_POW = r42.Z_POW


def verdict(d, rej, rb):
    if rej and d["dsum"] > 0 and all(x > 0 for x in d["windows"]) and rb["holds"]:
        return "(а) правило лучше базы — найдено на этих данных"
    if rej and d["dsum"] < 0:
        return "(б) правило хуже базы"
    if rej:
        return "(в) значимо, не устойчиво (окна / сутки / монета)"
    return "(г) не различимо: |ΔΣ| < MDE %.0f $ не исключено" % d["mde_sum"]


def cell_block(side, vid, bs):
    cs, kinds, pnl = load(side, vid)
    d = r42.delta(cs, bs)
    d["mde_sum"] = (Z_HOLM + Z_POW) * d["se_sum"]
    d["mde_mean"] = (Z_HOLM + Z_POW) * d["se_mean"]
    for k in ("mde_sum_holm", "mde_mean_holm"):
        d.pop(k)
    st_ = p8.cell_stats(cs, kinds, pnl)
    return cs, d, st_


def cmd_tables(a):
    res = dict(z_holm=Z_HOLM, z_pow=Z_POW, m_holm=M_HOLM, noise={}, bases={}, rows=[])
    n_cells = sum(len(rules_of(s)) * len(BASES[s]) for s in st.SIDES)
    for n in (13, M_HOLM, n_cells, 444):           # 444 — контекст: тесты TK-622, из которых отобраны зоны (критик, PASS 20.11)
        res["noise"][str(n)] = dict(zip(("emax_abs_z", "p95_max_abs_z"), r42.emax_abs_z(n)))
    for side in st.SIDES:
        for b in BASES[side]:
            bs, bk, bp = load(side, b + "__BASE")
            r = dict(base=p8.cell_stats(bs, bk, bp), cells={})
            for rid in rules_of(side):
                cs, d, s_ = cell_block(side, "%s__%s" % (b, rid), bs)
                r["cells"][rid] = dict(stats=s_, delta=d, robust=r42.robust(cs, bs, d["dsum"]))
            res["bases"][b] = r
    # семейство: 17 строк на решающих базах; Холм m = 17
    ps = [res["bases"][DECISION[RULES[rid][0]]]["cells"][rid]["delta"]["p2"] for _, _, rid in ROWS]
    rej = r42.holm(ps)
    for (no, met, rid), p, rj in zip(ROWS, ps, rej):
        c = res["bases"][DECISION[RULES[rid][0]]]["cells"][rid]
        res["rows"].append(dict(row=no, metric=met, rule=rid, base=DECISION[RULES[rid][0]], p2=p, holm_reject=bool(rj), verdict=verdict(c["delta"], rj, c["robust"])))
    # справочно: S_mk1, Холм m = 7 (строки стороны S)
    srows = [(no, met, rid) for no, met, rid in ROWS if RULES[rid][0] == "S"]
    ps2 = [res["bases"]["S_mk1"]["cells"][rid]["delta"]["p2"] for _, _, rid in srows]
    res["s_mk1_ref"] = [dict(row=no, rule=rid, p2=p, holm7_reject=bool(rj)) for (no, _, rid), p, rj in zip(srows, ps2, r42.holm(ps2))]
    r4.write_json(q("res", "tables.json"), res)
    for b, r in res["bases"].items():
        bb = r["base"]
        say("== %s: n %d, Σ %+.1f $, %+.3f $/сд., Sharpe %.2f, R:R %.2f, стоп %.2f %% маржи" % (b, bb["n"], bb["sum"], bb["mean"], bb["sharpe"], bb["rr"]["rr"], bb["rr"]["stops"]["mean_loss_pct"]))
        for rid, v in r["cells"].items():
            d, s_ = v["delta"], v["stats"]
            say("  %s %-5s n %4d Σ %+8.1f ΔΣ %+8.1f [%+.0f; %+.0f] p2 %.4f z %+.2f MDE %.0f Δ$/сд %+.3f Sh %.2f R:R %.2f окна %s" % (
                rid, RULES[rid][3], s_["n"], s_["sum"], d["dsum"], d["lo95"], d["hi95"], d["p2"], d["z"], d["mde_sum"], d["dmean"], s_["sharpe"], s_["rr"]["rr"],
                "/".join("%+.0f" % x for x in d["windows"])))
    for x in res["rows"]:
        d = res["bases"][x["base"]]["cells"][x["rule"]]["delta"]
        say("строка %2d %-30s %s %s ΔΣ %+.1f Δ$/сд %+.3f ± %.3f p2 %.4f Холм %s | %s" % (x["row"], x["metric"], x["rule"], x["base"], d["dsum"], d["dmean"], d["se_mean"], x["p2"], x["holm_reject"], x["verdict"]))
    say("шум: %s" % res["noise"])


COMMANDS = {"ftab": cmd_ftab, "cells": cmd_cells, "gate": cmd_gate, "counts": cmd_counts, "tables": cmd_tables}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--threads", type=int, default=THREADS)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
