#!/usr/bin/env python3
"""П-42 §13.60 (TK-660): вход по подтверждению E(k) у шорта на h775 — ключ движка `ek` (TK-660), портфель моделируется движком.
  python3 tk660_run.py variants   — клетки → res/variants60.json (без движка и без P&L)
  python3 tk660_run.py cells      — движок `breakout tick` (бинарь TK-660): строки баз L / S (ворота G-off) + клетки K шорта, окна wa / wb / wc
  python3 tk660_run.py tables     — G-bin, G-off, G-key, G-tape, G-dup, G-win; статистика клеток и парная Δ к базе → res/cells60-S.json
  python3 tk660_run.py verdict    — правило п. 7 (а)–(ж) → res/verdict60.json
Механика склейки, статистика, Холм, шум — `tk643_run` (§13.43) / `tk655_run` (§13.55) без изменений. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

ROOT = os.environ.get("TK660_ROOT", "/data/p42-tk660")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
BIN = ROOT + "/bin/alpha-tk660"                 # бинарь ветки TK-660 (ключ `ek`); sha256 — res/bin60.json
REF = "/data/p42-tk655"                         # строки баз того же пакета §13.55 (бинарь alpha-master) — эталон G-off
SIDE = "S"
K_VALS = (0, 5, 10, 15, 20, 25)                 # п. 5; 0 — база (ключ выключен), не клетка
PRIOR_TRIALS = 26                               # п. 7 (ж): семья «вход» шорта — 18 (§13.19, §13.24) + 8 (§13.55)
OFF_FILES = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv",
             "short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")
EK_META = "ek_[filled,expired,touched,rejected]"


def cells():
    """[клетка]; клетка = dict(cid, block, ax, patch). База (k = 0) не клетка."""
    return [dict(cid="K_k%d" % k, block="K", ax=dict(k=k), patch={"ek": k}) for k in K_VALS if k]


def neighbours(c, idx):
    """п. 7 (е): ближайшее k в каждую сторону; k = 0 (база, Δ ≡ 0) пропускается — у k = 5 сосед только справа."""
    vals = list(K_VALS)
    i = vals.index(c["ax"]["k"])
    res = []
    for seq in (vals[:i][::-1], vals[i + 1:]):
        for v in seq:
            if v == 0:
                continue
            if v in idx:
                res.append(idx[v])
            break
    return {"k": res} if res else {}


def trials():
    return len(cells()) + PRIOR_TRIALS


def verdict(rows):
    fam = cells()
    ids = [c["cid"] for c in fam]
    rej = dict(zip(ids, t.holm([rows[c]["delta"]["p"] for c in ids])))
    dmap = {c: rows[c]["delta"]["d"] for c in ids}
    idx = {c["ax"]["k"]: c["cid"] for c in fam}
    nt = trials()
    res = {}
    for c in fam:
        cid, r = c["cid"], rows[c["cid"]]
        dl = r["delta"]
        g = {}
        g["a"] = r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH))
        g["b"] = bool(rej[cid])
        g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t.NEED_MONTHS
        g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
        g["e"] = dl["s_c"] > 0 and dl["dsum"] > 0                       # §13.55.1 п. 1 для K: ΔΣ > 0
        nb = neighbours(c, idx)
        g["f"] = dl["d"] > 0 and t.plateau_ok(dl["d"], nb, dmap)
        nz = t.noise_bar(r["sharpe"], nt)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz,
                        by_selection=bool(dl["d"] > 0 and r["n"] < rows["_base_n"]))
    passed = [c for c in ids if res[c]["passed"]]
    return dict(side=SIDE, m=len(ids), trials_noise=nt, passed=passed, cells=res,
                answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база шорта остаётся")


# ---- движок и таблицы (srv) -------------------------------------------------------------------------------------------------------
def cmd_variants(a):
    c = cells()
    json.dump(dict(cells=c, m=len(c), trials_noise=trials()), open(t.q("res", "variants60.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("клеток S: %d (K); N шума %d" % (len(c), trials()))


def _bin():
    import tk604_run as r4
    r4.BIN = BIN                                # r5.tick берёт r4.BIN при вызове
    sha = r4.sha256(BIN)
    json.dump(dict(bin=BIN, sha256=sha), open(t.q("res", "bin60.json"), "w", encoding="utf-8"), indent=1)
    return sha


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    t.say("G-bin: %s sha256 %s" % (BIN, _bin()))
    u = r5.Uni("h775")
    t.run_variants(u, "L", [("base", t.variant_line(r5, u, "L", {}))], "off")
    v = [("base", t.variant_line(r5, u, SIDE, {}))] + [(c["cid"], t.variant_line(r5, u, SIDE, c["patch"])) for c in cells()]
    t.run_variants(u, SIDE, v, "k")
    t.say("cells готово")


def g_off(st):
    """G-off: строки баз (ключ выключен) бинарём TK-660 = строкам баз пакета §13.55 (alpha-master) байт в байт, все CSV обеих сторон."""
    bad = []
    for side in ("L", "S"):
        for w in st.TRADE_WINDOWS:
            a_, b_ = t.cell_dir(w.name, side, "base"), os.path.join(REF, "cells", "%s-%s" % (w.name, side), "base")
            for f in OFF_FILES:
                if not filecmp.cmp(os.path.join(a_, f), os.path.join(b_, f), shallow=False):
                    bad.append("%s-%s/%s" % (w.name, side, f))
            ma, mb = (json.load(open(os.path.join(d, "meta.json"), encoding="utf-8")) for d in (a_, b_))
            if ma != mb:
                bad.append("%s-%s/meta.json" % (w.name, side))
    return bad


def ek_counts(st, cid):
    tot = [0, 0, 0, 0]
    for w in st.TRADE_WINDOWS:
        m = json.load(open(os.path.join(t.cell_dir(w.name, SIDE, cid), "meta.json"), encoding="utf-8"))["short"]
        tot = [x + y for x, y in zip(tot, m[EK_META])]
    return dict(zip(("filled", "expired", "touched", "rejected"), tot))


def cmd_tables(a):
    st, r5, p8, r34 = t._srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    sha = json.load(open(t.q("res", "bin60.json"), encoding="utf-8"))["sha256"]
    off = g_off(st)
    t.say("G-off: %s" % ("ok — 6 строк баз (L, S × 3 окна), 8 CSV + meta.json байт в байт" if not off else "ОШИБКА " + ", ".join(off)))
    if off:
        bad.append("G-off")
    bser, bk, bp = t.load_cell(st, p8, u, SIDE, {w.name: (u.base(w.name, SIDE), SIDE) for w in st.TRADE_WINDOWS})
    bkeys = list(p8.keys_of(bser))
    bmap = dict(zip(bkeys, bp))
    out = dict(side=SIDE, bin_sha=sha, goff=not off, base=p8.cell_stats(bser, bk, bp), cells={})
    sig, tmax = {}, 0
    for c in cells():
        ser, kinds, pnl = t.load_cell(st, p8, u, SIDE, t.load_dirs(st, SIDE, c["cid"]))
        r = t.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
        keys = list(p8.keys_of(ser))
        ks = set(keys)
        gone = [k for k in bkeys if k not in ks]
        common = [(k, x) for k, x in zip(keys, pnl) if k in bmap]
        same = dict(only_cell=len(ks - set(bkeys)), only_base=len(gone), common=len(common))
        changed = int(sum(1 for k, x in common if abs(x - bmap[k]) > 1e-6))
        if same["only_cell"] + same["only_base"] + changed == 0:
            bad.append("G-key %s: сделки и P&L равны базе" % c["cid"])
        r.update(block=c["block"], ax=c["ax"], patch=c["patch"], same=same, pnl_changed=changed, ek=ek_counts(st, c["cid"]),
                 skipped_base_n=len(gone), skipped_base_sum=float(sum(bmap[k] for k in gone)),
                 new_cell_sum=float(sum(x for k, x in zip(keys, pnl) if k not in bmap)))
        out["cells"][c["cid"]] = r
        sig.setdefault((tuple(keys), tuple(np.round(pnl, 6))), []).append(c["cid"])
        for w in st.TRADE_WINDOWS:
            tb = st.read_trades_csv(os.path.join(t.cell_dir(w.name, SIDE, c["cid"]), "short-trades.csv"))
            if len(tb.exit_ms):
                tmax = max(tmax, int(tb.exit_ms.max()) + w.t0_ms)
    out["tape_max_exit_ms"] = tmax
    if tmax >= st.LAST_MS:
        bad.append("G-tape: выход сделки клетки позже 13.11")
    out["gdup"] = [v for v in sig.values() if len(v) > 1]
    out["gwin_forced"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if r["kinds_n"].get("forced", 0)}
    out["gwin_forced_base"] = int(sum(1 for k in bk if k == "forced"))
    json.dump(out, open(t.q("res", "cells60-S.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    t.say("== S: база %d сделок; клеток %d; G-dup групп %d; последний выход клеток %d" % (out["base"]["n"], len(out["cells"]), len(out["gdup"]), tmax))
    if bad:
        t.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t.say("tables готово")


def cmd_verdict(a):
    tab = json.load(open(t.q("res", "cells60-S.json"), encoding="utf-8"))
    rows = dict(tab["cells"], _base_n=tab["base"]["n"])
    v = verdict(rows)
    t.say("== S: m = %d, N шума = %d; %s" % (v["m"], v["trials_noise"], v["answer"]))
    json.dump(v, open(t.q("res", "verdict60.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    COMMANDS[ap.parse_args().cmd](None)


if __name__ == "__main__":
    main()
