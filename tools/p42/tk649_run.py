#!/usr/bin/env python3
"""П-42 §13.49 (TK-649): выход «уровень не тронут за T минут от входа» (ключ движка `tu`) на h775, обе стороны, против `L_base` / `S_base`.
  python3 tk649_run.py gbyte    — G-bin + G-byte: базы `L_base` / `S_base` новым бинарём (без `tu`) × окна wa / wb / wc = готовые базы h775 байт в байт (P&L не новый)
  python3 tk649_run.py cells    — движок `breakout tick`: клетки T × стороны × окна (только после PASS критика)
  python3 tk649_run.py tables   — G-base, G-off, G-dup, статистика клеток и парная Δ к базе → res/cells49-<сторона>.json
  python3 tk649_run.py verdict  — правило п. 7 (Холм по 12 клеткам обеих сторон, месяцы, устойчивость, сумма, плато по T, шум) → res/verdict49.json
  python3 tk649_run.py gwin     — сделки, закрытые концом данных окна, и наибольшее удержание → res/gwin49.json
Доллары — ставка 500 $; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан. Статистика и ворота — функции `tk643_run` (§13.43) без изменений."""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
sys.path.insert(0, HERE)
import tk643_run as t3  # noqa: E402  (load_cell, stat_row, holm, noise_bar, plateau_ok, MIN_N, MIN_MONTH, NEED_MONTHS — §13.43 как есть)

ROOT = os.environ.get("TK649_ROOT", "/data/p42-tk649")
BIN = os.path.join(ROOT, "bin", "alpha-tk649-plain")
SIDES = ("L", "S")
T_VALS = (5, 15, 30, 60, 120, 240)              # п. 5: минуты от входа
PRIOR_TRIALS = {"L": 312 + 80, "S": 312 + 80}  # п. 7 (ж): клетки стопа / выходов стороны TK-643 на h775 (312) + прежние на 566 (80)
SIGSX_DAYS = "14:73"                            # как `tk625_run.cmd_eng` для wa / wb / wc


def say(*a):
    print(*a, flush=True)


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def cells():
    """[(cid, T)] одной стороны (одинаковы у L и S)."""
    return [("tu%d" % v, v) for v in T_VALS]


def neighbours(cid):
    """Соседи по оси T (п. 7 (е)): ближайшие значения сетки в обе стороны; за краем 240 — база (выкл., Δ ≡ 0) — направление выпадает."""
    i = [c for c, _ in cells()].index(cid)
    ids = [c for c, _ in cells()]
    nb = [ids[j] for j in (i - 1, i + 1) if 0 <= j < len(ids)]
    return {"T": nb}


def _srv():
    import tk604_run as r4
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    import tk634_run as r34
    return r4, st, r5, p8, r34


def tick(r4, r5, u, w, variants, out, env, log):
    """`tk625_run.tick` с бинарём TK-649 (ключ `tu`)."""
    d = u.wdir(w.name)
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8") as f:
        f.write("".join("%s %s\n" % v for v in variants))
    cmd = [BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf, "--symbols",
           open(u.coins_file, encoding="utf-8").read().strip(), "--out", out, "--threads", str(r4.THREADS)] + r5.TICK_STORE
    e = {"P42_T0": w.t0}
    e.update(env)
    r4.run(cmd, e, log)


def base_line(r5, u, side, tu=0):
    return "%s%s%s" % (r5.BASE[side], u.extra, (" tu=%d" % tu) if tu else "")


def meta_equal(new_path, old_path):
    """meta.json: все поля равны; строка `params` — равна, либо отличается только полем `tl: 0` (v22 TK-603 добавлен в `Debug` после сборки готовой базы)."""
    a, b = json.load(open(new_path, encoding="utf-8")), json.load(open(old_path, encoding="utf-8"))
    pa, pb = a.pop("params", None), b.pop("params", None)
    if a != b:
        return False, "поля meta.json кроме params различаются"
    if pa == pb:
        return True, "params равны"
    if pa is not None and pa.replace(", tl: 0 }", " }", 1) == pb:
        return True, "params: отличие только в поле tl: 0"
    return False, "params различаются"


def cmd_gbyte(a):
    r4, st, r5, _, _ = _srv()
    sha = r4.sha256(BIN)
    u = r5.Uni("h775")
    res, bad = {"bin_sha256": sha, "windows": {}}, []
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "gbyte", w.name)
        if not os.path.exists(out + "/DONE"):
            tick(r4, r5, u, w, [(r5.VID[s], base_line(r5, u, s)) for s in SIDES], out, {"P42_SIGSX": SIGSX_DAYS}, q("log", "gbyte-%s.log" % w.name))
            open(out + "/DONE", "w").close()
        ref = os.path.dirname(u.base(w.name, "L"))
        rw = {}
        for s in SIDES:
            vid = r5.VID[s]
            nd, od = os.path.join(out, vid), os.path.join(ref, vid)
            files = sorted(set(os.listdir(nd)) | set(os.listdir(od)))
            diff = []
            for fn in files:
                pn, po = os.path.join(nd, fn), os.path.join(od, fn)
                if not (os.path.exists(pn) and os.path.exists(po)):
                    diff.append(fn + ": нет в одном из каталогов")
                elif fn == "meta.json":
                    ok, why = meta_equal(pn, po)
                    rw.setdefault(vid + "/meta", why)
                    if not ok:
                        diff.append("meta.json: " + why)
                elif not filecmp.cmp(pn, po, shallow=False):
                    diff.append(fn + ": байты различаются")
            rw[vid] = dict(files=len(files), diff=diff)
            bad += ["%s %s: %s" % (w.name, vid, x) for x in diff]
        sn, so = os.path.join(out, "summary.csv"), os.path.join(ref, "summary.csv")
        rw["summary.csv"] = os.path.exists(sn) and os.path.exists(so) and filecmp.cmp(sn, so, shallow=False)
        if not rw["summary.csv"]:
            bad.append("%s summary.csv: байты различаются" % w.name)
        res["windows"][w.name] = rw
    res["ok"] = not bad
    res["bad"] = bad
    json.dump(res, open(q("res", "gbyte49.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("G-byte: %s; sha256 %s" % ("ok" if not bad else "ОШИБКА " + "; ".join(bad), sha))
    if bad:
        sys.exit(1)


def cmd_cells(a):
    r4, st, r5, _, _ = _srv()
    g = json.load(open(q("res", "gbyte49.json"), encoding="utf-8"))
    if not g["ok"] or g["bin_sha256"] != r4.sha256(BIN):
        raise SystemExit("G-byte не пройден или бинарь сменился после G-byte")
    u = r5.Uni("h775")
    for side in SIDES:
        v = [(cid, base_line(r5, u, side, T)) for cid, T in cells()]
        for w in st.TRADE_WINDOWS:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            done = os.path.join(ROOT, "done", "%s-%s" % (w.name, side))
            if os.path.exists(done):
                continue
            tick(r4, r5, u, w, v, out, {}, q("log", "cells-%s-%s.log" % (w.name, side)))
            os.makedirs(os.path.dirname(done), exist_ok=True)
            open(done, "w").write("%d\n" % len(v))
            say("cells %s-%s готово" % (w.name, side))
    say("cells готово")


def cell_dirs(st, side, cid):
    return {w.name: (os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), cid), side) for w in st.TRADE_WINDOWS}


def cmd_tables(a):
    r4, st, r5, p8, r34 = _srv()
    import numpy as np
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    for side in SIDES:
        bser, bk, bp = t3.load_cell(st, p8, u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        gser, gk, gp = t3.load_cell(st, p8, u, side, {w.name: (os.path.join(ROOT, "gbyte", w.name, r5.VID[side]), side) for w in st.TRADE_WINDOWS})
        gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(np.allclose(gp, bp, atol=1e-6))
        if not gb:
            bad.append("G-base %s: база новым бинарём ≠ готовой базе h775" % side)
        if any(k == "tu" for k in bk):
            bad.append("G-off %s: у базы есть выходы tu" % side)
        out = dict(side=side, base=p8.cell_stats(bser, bk, bp), gbase=gb, cells={})
        sig = {}
        for cid, T in cells():
            ser, kinds, pnl = t3.load_cell(st, p8, u, side, cell_dirs(st, side, cid))
            r = t3.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            r["T"] = T
            out["cells"][cid] = r
            if not r["kinds_n"].get("tu", 0):
                bad.append("G-off %s %s: при tu=%d нет ни одного выхода tu" % (side, cid, T))
            sig.setdefault((tuple(p8.keys_of(ser)), tuple(np.round(pnl, 6))), []).append(cid)
        out["gdup"] = [v for v in sig.values() if len(v) > 1]
        json.dump(out, open(q("res", "cells49-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        say("== %s: база %d сделок, Σ %+.2f $; G-base %s; клеток %d; G-dup групп %d" % (
            side, out["base"]["n"], out["base"]["sum"], "ok" if gb else "ОШИБКА", len(out["cells"]), len(out["gdup"])))
    if bad:
        say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    say("tables готово")


def verdict(rows):
    """rows: {сторона: {cid: строка статистики}}. Холм — по всем 12 клеткам обеих сторон (п. 7 (б)); остальное — как §13.43 п. 7."""
    ids = [(s, cid) for s in SIDES for cid, _ in cells()]
    rej = dict(zip(ids, t3.holm([rows[s][c]["delta"]["p"] for s, c in ids])))
    res = {}
    for side in SIDES:
        R = rows[side]
        dmap = {c: R[c]["delta"]["d"] for c, _ in cells()}
        trials = len(cells()) + PRIOR_TRIALS[side]
        cres = {}
        for cid, _ in cells():
            r, dl = R[cid], R[cid]["delta"]
            g = {}
            g["a"] = r["n"] >= t3.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t3.MIN_MONTH))
            g["b"] = bool(rej[(side, cid)])
            g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t3.NEED_MONTHS
            g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
            g["e"] = dl["s_c"] > 0 and dl["dsum"] >= -1.0 * dl["se_sum"]
            nb = neighbours(cid)
            g["f"] = dl["d"] > 0 and t3.plateau_ok(dl["d"], nb, dmap)
            nz = t3.noise_bar(r["sharpe"], trials)
            g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
            cres[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
        passed = [c for c, _ in cells() if cres[c]["passed"]]
        res[side] = dict(side=side, m_holm=len(ids), trials_noise=trials, passed=passed, cells=cres,
                         answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база стороны остаётся")
    return res


def cmd_verdict(a):
    rows = {s: json.load(open(q("res", "cells49-%s.json" % s), encoding="utf-8"))["cells"] for s in SIDES}
    res = verdict(rows)
    for s in SIDES:
        v = res[s]
        say("== %s: Холм m = %d, N шума = %d; %s" % (s, v["m_holm"], v["trials_noise"], v["answer"]))
        for g in "abcdefg":
            say("   ворота (%s) прошли %d из %d" % (g, sum(1 for c in v["cells"].values() if c["gates"][g]), len(v["cells"])))
    json.dump(res, open(q("res", "verdict49.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


def cmd_gwin(a):
    _, st, r5, p8, _ = _srv()
    u = r5.Uni("h775")
    out = {}
    for side in SIDES:
        res = {}
        for cid in ["base"] + [c for c, _ in cells()]:
            dirs = {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS} if cid == "base" else cell_dirs(st, side, cid)
            ser, kinds, pnl = t3.load_cell(st, p8, u, side, dirs)
            hold = (ser.t_out - ser.t_in) / 86_400_000.0
            res[cid] = dict(forced=int(sum(1 for k in kinds if k == "forced")), max_hold_d=float(hold.max()) if len(hold) else 0.0)
        out[side] = res
        say("== %s: forced %s" % (side, {c: r["forced"] for c, r in res.items() if r["forced"]}))
    json.dump(out, open(q("res", "gwin49.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


COMMANDS = {"gbyte": cmd_gbyte, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict, "gwin": cmd_gwin}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
