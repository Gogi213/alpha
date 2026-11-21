#!/usr/bin/env python3
"""П-42 §13.68 / §13.68.1 / §13.68.2 (TK-668): P&L — только после PASS критика (файл `/data/p42-tk668/PASS`). Подкоманды (порядок — §13.68 п. 8):
  cells a      — движок `breakout tick`: ALL1 (контроль) + допущенные пробные клетки E5 / EP / B (`--ftab`, `fa=<клетка> fo=lo fv=0.5`), окна wa / wb / wc. P&L не читает
  gate         — G8 (no_key = 0, dropped = пересчёту), G-base (ALL1 = база байт в байт), G-ref (составная таблица `N24_q60` без клетки = `L_n24` TK-638 байт в байт). P&L не читает
  select a     — (g1)–(g4) пробных клеток: Холм E m = 79, B m = 121; список клеток, чьи соседи считаются (`res/stageA68.json`)
  cells b      — соседи клеток, прошедших (g1)–(g4) (может быть пусто)
  select final — (g1)–(g5), исход гипотез × стороны (п. 7), MDE, шумовая планка → `res/select68.json`
  cells ref    — справочно на `L_n24`: составные таблицы (`N24_q60` И главная клетка) E5 / EP лонга; `ref` — таблица
  step1        — ступень 1 (0 испытаний): срезы сделок баз по `natr` × `R` и терцилям `X24` / `X7` / `XP` / `XV`
  e8           — линия E8: повтор по ленте (правило и соседи — §13.68 п. 5, §13.68.1 п. 2), ворота линии (l1)–(l5), «база + E8» → `res/e8-68.json`"""
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

import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk625_stats as s5  # noqa: E402
import tk633_run as r33  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk642_run as k2  # noqa: E402
import tk668_cells as cl  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk668_run as r  # noqa: E402
import selection_bias as sb  # noqa: E402  (путь — через tk638_post)

ROOT = r.ROOT
SIDES, ENG, BASE = st.SIDES, r5.ENG, r5.BASE
GuardError = st.GuardError
say, sha256 = r4.say, r4.sha256
FV = 0.5
FILES = ("trades", "daily", "coinday", "entryrej")
ALPHA = 0.05
OLD_SELECT = ("/data/p42-tk625/res/select.json", "/data/p42-tk633/res/select33.json", "/data/p42-tk634/res/select34.json", "/data/p42-tk636/res/select36.json")
TABLES56 = "/data/p42-tk656/res/tables56.json"
REF_FTAB = "/data/p42-tk638/ftab38-h775/%s-L.csv"
REF_DIR = "/data/p42-tk638/ladd-h775/%s-L/N24_q60"
M_E, M_B, M_L = 79, 121, 7                                   # §13.68 п. 6: E 61 + 18; B 61 + 24 + 18 + 18; линии E8 + E4 (6), E30 — DROP
HYP = {"14 (X24H + X7H)": ["X24H", "X7H"], "37 (X24L)": ["X24L"], "15 (XPH + XPL)": ["XPH", "XPL"], "44 (XVH + XVL)": ["XVH", "XVL"], "5 (RM10)": ["RM10"],
       "56 h = 1 мин": ["Bsk_1", "Bwi_1", "Bag_1"], "56 h = 5 мин": ["Bsk_5", "Bwi_5", "Bag_5"], "56 h = 15 мин": ["Bsk_15", "Bwi_15", "Bag_15"]}
REF_CELLS = ("RM10", "X24H", "X24L", "X7H", "X7L", "XPH", "XPL", "XVH", "XVL")
DAY_MS = 86_400_000
D0_MS = int(datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
SPECS = {sp["cid"]: sp for sp in cl.cell_specs()}


def need_pass():
    if not os.path.exists(os.path.join(ROOT, "PASS")):
        raise GuardError("нет %s/PASS — P&L не читается до PASS критика на §13.68.2" % ROOT)


def counts_of(side):
    return json.load(open(os.path.join(ROOT, "res", "counts68-%s.json" % side), encoding="utf-8"))


def grp_dir(group, wname, side):
    return os.path.join(ROOT, "cells-" + group, "%s-%s" % (wname, side))


def ref_ftab(wname):
    return os.path.join(ROOT, "ftab-ref", "%s-L.csv" % wname)


def stage_a():
    p = os.path.join(ROOT, "res", "stageA68.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def run_cids(side, group):
    c = counts_of(side)["cells"]
    if group == "a":
        return ["ALL1"] + [x["cid"] for x in c if x["trial"] and x["admit_floor"] and x["dup_of"] is None]
    if group == "b":
        sa = stage_a()
        if sa is None:
            raise GuardError("нет res/stageA68.json — сначала `select a`")
        hosts = set(sa["hosts"][side])
        return [x["cid"] for x in c if not x["trial"] and x["group"] in hosts and x["admit_floor"] and x["dup_of"] is None]
    if group == "ref":
        return ["REF"] + ["N24_" + cid for cid in REF_CELLS] if side == "L" else []
    raise GuardError("группа %s" % group)


def variants(u, side, cids):
    return [(cid, "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, cid, FV)) for cid in cids]


def ftab_of(group, wname, side):
    return ref_ftab(wname) if group == "ref" else r.ftab_path(wname, side)


def build_ref_ftab(wname):
    """Составная таблица на L: `REF` = `N24_q60` TK-638, `N24_<клетка>` = `N24_q60` И клетка (1,0 / 0,0); ключи (монета, мс) совпадают с таблицей признаков 1 в 1."""
    import pandas as pd
    mine = pd.read_csv(r.ftab_path(wname, "L"), dtype={"sym": str})
    n24 = pd.read_csv(REF_FTAB % wname, usecols=["sym", "sig_ms", "N24_q60"], dtype={"sym": str})
    m = mine.merge(n24, on=["sym", "sig_ms"], how="outer", indicator=True)
    if len(m) != len(mine) or len(m) != len(n24) or (m["_merge"] != "both").any():
        raise GuardError("ref %s: ключи таблицы клеток (%d) и N24 TK-638 (%d) не совпадают" % (wname, len(mine), len(n24)))
    cols = {"REF": m["N24_q60"].to_numpy(float)}
    for cid in REF_CELLS:
        cols["N24_" + cid] = np.minimum(m["N24_q60"].to_numpy(float), m[cid].to_numpy(float))
    path = r.qq("ftab-ref", "%s-L.csv" % wname)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(cols) + "\n")
        for i in range(len(m)):
            f.write("%s,%d,%s\n" % (m["sym"].iloc[i], int(m["sig_ms"].iloc[i]), ",".join("%.1f" % cols[k][i] for k in cols)))
    return path


def cmd_cells(a):
    need_pass()
    g = a.arg
    u = r.uni()
    for w in st.TRADE_WINDOWS:
        st.check_dates(w.t0, w.hi)
        for side in SIDES:
            cids = run_cids(side, g)
            out = grp_dir(g, w.name, side)
            if os.path.exists(out + "/DONE"):
                continue
            if not cids:
                os.makedirs(out, exist_ok=True)
                open(out + "/DONE", "w").write("0 -\n")
                continue
            if g == "ref":
                build_ref_ftab(w.name)
            ftab = ftab_of(g, w.name, side)
            v = variants(u, side, cids)
            r5.tick(u, w, v, out, {}, r.qq("log", "cells68%s-%s-%s.log" % (g, w.name, side)), ftab=ftab)
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(v), sha256(ftab)))
            say("cells68-%s %s-%s: %d вариантов готово" % (g, w.name, side, len(v)))


def done_groups(side):
    return [g for g in ("a", "b", "ref") if all(os.path.exists(grp_dir(g, w.name, side) + "/DONE") for w in st.TRADE_WINDOWS)]


def cmd_gate(a):
    need_pass()
    u = r.uni()
    bad, res = [], dict(g8={}, gbase={}, gref={})
    for side in SIDES:
        for g in done_groups(side):
            cids = run_cids(side, g)
            for w in st.TRADE_WINDOWS:
                ftab = ftab_of(g, w.name, side)
                dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
                dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
                for cid in cids:
                    tab = r34.read_table_col(ftab, cid)
                    miss = sum(1 for k in dkeys if k not in tab)
                    exp_u = sum(1 for v in tab.values() if v == v and v < FV)
                    exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV)
                    m = json.load(open(os.path.join(grp_dir(g, w.name, side), cid, "meta.json"), encoding="utf-8"))[ENG[side]]
                    fc = r4.filter_counters(m)
                    res["g8"]["%s/%s-%s/%s" % (g, w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                    if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                        bad.append("G8 %s/%s-%s/%s: %s, ожидалось %d / %d, без строки %d" % (g, w.name, side, cid, fc, exp_u, exp_m, miss))
                for fn in FILES:
                    name = ENG[side] + "-%s.csv" % fn
                    if g == "a":
                        x, y = os.path.join(grp_dir(g, w.name, side), "ALL1", name), os.path.join(u.base(w.name, side), name)
                        same = os.path.exists(x) and os.path.exists(y) and filecmp.cmp(x, y, shallow=False)
                        res["gbase"]["%s-%s/%s" % (w.name, side, fn)] = bool(same)
                        if not same:
                            bad.append("G-base %s-%s %s" % (w.name, side, fn))
                    if g == "ref" and cids:
                        x, y = os.path.join(grp_dir(g, w.name, side), "REF", name), os.path.join(REF_DIR % w.name, name)
                        same = os.path.exists(x) and os.path.exists(y) and filecmp.cmp(x, y, shallow=False)
                        res["gref"]["%s-%s/%s" % (w.name, side, fn)] = bool(same)
                        if not same:
                            bad.append("G-ref %s-%s %s" % (w.name, side, fn))
    res["bad"] = bad
    r4.write_json(r.qq("gates", "g68-pnl.json"), res)
    say("G8 + G-base + G-ref:", "FAIL " + "; ".join(bad[:8]) if bad else "ok: %d прогонов G8, %d файлов G-base, %d файлов G-ref" % (len(res["g8"]), len(res["gbase"]), len(res["gref"])))
    if bad:
        sys.exit(1)


# ---- ряды и клетки ------------------------------------------------------------------------------------------------------------------

def load_base(u, side):
    return p8.load_cell(u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})


def load_cell(u, side, group, cid):
    return p8.load_cell(u, side, {w.name: (os.path.join(grp_dir(group, w.name, side), cid), side) for w in st.TRADE_WINDOWS})


def aligned_values(u, side, ser):
    """Значения признаков на сделках базы в порядке ряда базы (ключ (монета, абсолютное мс сигнала)); охрана: тот же набор и те же сутки."""
    tr = r.base_trades(u, side)
    wmap = {w.name: w for w in st.TRADE_WINDOWS}
    pos = {}
    for i, (s_, k_) in enumerate(zip(ser.sym_name, ser.key_ms)):
        if (s_, int(k_)) in pos:
            raise GuardError("ряд базы %s: дубль ключа %s" % (side, s_))
        pos[(s_, int(k_))] = i
    if len(tr) != len(ser):
        raise GuardError("%s: сделок базы %d ≠ ряд %d" % (side, len(tr), len(ser)))
    idx = np.empty(len(tr), dtype=np.int64)
    for j, x in enumerate(tr):
        i = pos.get((x["sym"], wmap[x["w"]].t0_ms + x["sig_ms"]))
        if i is None:
            raise GuardError("%s: сделка %s %d не найдена в ряду базы" % (side, x["sym"], x["sig_ms"]))
        idx[j] = i
    if not np.array_equal(ser.day[idx], np.array([x["day"] for x in tr])):
        raise GuardError("%s: сутки сделок базы ≠ ряд" % side)
    cols, _ = r.trade_feat(u, side)
    out = {}
    for c, v in cols.items():
        o = np.full(len(tr), np.nan)
        o[idx] = v
        out[c] = o
    return out


def build_side(u, side, groups):
    """SideCells: база + клетки (ряд есть у запущенных; недопущенные и не запущенные — без ряда, в Холм входят с p = 1)."""
    base = load_base(u, side)
    vals = aligned_values(u, side, base[0])
    cnt = counts_of(side)
    dup = {c["cid"]: c["dup_of"] for c in cnt["cells"]}
    full = {}
    for g in groups:
        for cid in run_cids(side, g):
            if cid != "ALL1":
                full[cid] = load_cell(u, side, g, cid)
    cells = [s5.Cell(sp["cid"], "-", sp["group"], 0.0, None, sp["trial"], dup[sp["cid"]], full[sp["cid"]][0] if sp["cid"] in full else None,
                     cl.excl_mask(sp, vals[sp["col"]], cnt["thr"])) for sp in cl.cell_specs()]
    return s5.SideCells(base[0], cells, len(u.coins())), base, full, vals


def old_ps():
    ps = []
    for p_ in OLD_SELECT:
        ps += json.load(open(p_, encoding="utf-8"))["holm"]["ps"]
    if len(ps) != 61:
        raise GuardError("прежних пробных клеток %d ≠ 61 (§13.68 п. 6)" % len(ps))
    t56 = json.load(open(TABLES56, encoding="utf-8"))
    p56 = [c["delta"]["p2"] for s in SIDES for c in t56["sides"][s]["cells"].values()]
    if len(p56) != 24:
        raise GuardError("p TK-656: %d ≠ 24" % len(p56))
    return ps, p56


def new_family(ev, fam):
    out = []
    for s in SIDES:
        for sp in cl.cell_specs():
            if sp["trial"] and sp["family"] == fam:
                row = ev["rows"][s].get(sp["cid"])
                ok = row is not None and row["admitted"] and row["p"] == row["p"]
                out.append((s, sp["cid"], float(row["p"]) if ok else 1.0))
    return out


def joint_families(ev):
    """(g1) по семействам §13.68 п. 6: E — 61 прежних + 18 новых (m = 79); B — 61 + 24 (TK-656) + 18 (E) + 18 (B) (m = 121). Недопущенные и не запущенные входят с p = 1."""
    ps61, p56 = old_ps()
    fe, fb = new_family(ev, "E"), new_family(ev, "B")
    if len(fe) != 18 or len(fb) != 18:
        raise GuardError("новых пробных клеток E %d, B %d ≠ 18 / 18" % (len(fe), len(fb)))
    pe, pb = [p for *_, p in fe], [p for *_, p in fb]
    rej = {"E": st.holm_reject(ps61 + pe, ALPHA)[61:], "B": st.holm_reject(ps61 + p56 + pe + pb, ALPHA)[-18:]}
    rej_new = {"E": st.holm_reject(pe, ALPHA), "B": st.holm_reject(pb, ALPHA)}
    z = {"E": k2.zq(1 - ALPHA / M_E), "B": k2.zq(1 - ALPHA / M_B)}
    out = {}
    for fam, items in (("E", fe), ("B", fb)):
        for i, (s, cid, p) in enumerate(items):
            row = ev["rows"][s].get(cid)
            out[(s, cid)] = dict(family=fam, m=M_E if fam == "E" else M_B, p_used=p, reject=bool(rej[fam][i]), reject_new_only=bool(rej_new[fam][i]), has_row=row is not None)
            if row is None:
                continue
            ok = bool(row["admitted"] and row["d"] > 0)
            row["g1"], row["g1_new_only"] = bool(rej[fam][i] and ok), bool(rej_new[fam][i] and ok)
            row["family"], row["z_holm"] = fam, z[fam]
            row["mde_mean"] = (z[fam] + k2.Z_POW) * row["se"] if row["admitted"] else float("nan")
            row["mde_sum"] = (z[fam] + k2.Z_POW) * row["se_sum"] if row["admitted"] else float("nan")
            row["pass"] = bool(row["admitted"] and row["g1"] and row.get("g2") and row.get("g3") and row.get("g4") and row.get("g5"))
    return out


def outcome(rows, cids):
    """Исход гипотезы × стороны по §13.68 п. 7."""
    rs = [rows.get(c) for c in cids]
    adm = [x for x in rs if x and x["admitted"]]
    if not adm:
        return "не проверяемо: клетки не допущены (мало исключённых или сделок)"
    if any(x.get("pass") for x in adm):
        return "добавляет $ (найдено на этих данных; кандидат только вперёд)"
    if any(x.get("g1") and x.get("g2") and x.get("g4") and not x.get("g3") for x in adm):
        return "качество сделок выше, Σ не выше (подмножество; не добавляет $)"
    if all(x["d"] < 0 for x in adm):
        return "не работает (все допущенные клетки Δ < 0, точка)"
    return "не ясно: |Δ| < MDE %.2f $/сделку" % min(x["mde_mean"] for x in adm)


def trial_cids(fam=None):
    return [sp["cid"] for sp in cl.cell_specs() if sp["trial"] and (fam is None or sp["family"] == fam)]


def noise_bar(n):
    return k2.emax_abs_z(n, seed=668)


def cmd_select(a):
    need_pass()
    final = a.arg == "final"
    u = r.uni()
    allom = np.arange(st.NDAYS)
    groups = ["a", "b"] if final else ["a"]
    sides, base, full = {}, {}, {}
    for s in SIDES:
        sides[s], base[s], full[s], _ = build_side(u, s, groups)
    ev = s5.evaluate(sides, allom)
    fam = joint_families(ev)
    bs = {s: p8.cell_stats(*base[s]) for s in SIDES}
    for s in SIDES:
        b = bs[s]
        say("R:R базы %s: %.2f (средний выигрыш %+.2f $, средний убыток %+.2f $); стопов %d, убыток стопа средний %+.2f $ (%.2f %% маржи 250 $), макс. %+.2f $ (%.2f %% маржи)" % (
            s, b["rr"]["rr"], b["rr"]["mean_win"], b["rr"]["mean_loss"], b["rr"]["stops"]["n"], b["rr"]["stops"]["mean_loss"], b["rr"]["stops"]["mean_loss_pct"],
            b["rr"]["stops"]["max_loss"], b["rr"]["stops"]["max_loss_pct"]))
    n_run = {f: sum(1 for s in SIDES for sp in cl.cell_specs() if sp["family"] == f and ev["rows"][s].get(sp["cid"]) is not None) for f in ("E", "B")}
    bars = {f: noise_bar(n) for f, n in n_run.items()}
    say("Холм: E m = %d (первый шаг %.5f), B m = %d (первый шаг %.5f); клеток на P&L: E %d, B %d; шумовая планка max|z|: E %.2f (p95 %.2f), B %.2f (p95 %.2f)" % (
        M_E, ALPHA / M_E, M_B, ALPHA / M_B, n_run["E"], n_run["B"], bars["E"][0], bars["E"][1], bars["B"][0], bars["B"][1]))
    cells_out = {}
    for s in SIDES:
        say("== %s: база n %d, Σ %+.1f $, %+.3f $/сделку, Sharpe %.2f" % (s, bs[s]["n"], bs[s]["sum"], bs[s]["mean"], bs[s]["sharpe"]))
        cells_out[s] = {}
        for sp in cl.cell_specs():
            cid = sp["cid"]
            row = ev["rows"][s].get(cid)
            if row is None:
                if sp["trial"]:
                    say("   %-10s пробная: не допущена / не запущена (p = 1) — %s" % (cid, cl.cell_label(sp)))
                continue
            cs = p8.cell_stats(*full[s][cid])
            bar = bars[sp["family"]][0]
            sn = sb.significance(cs["sharpe"], float(np.sqrt(365.0 / st.NDAYS * (1 + cs["sharpe"] ** 2 / 730.0))), n_run[sp["family"]])
            row["noise_bar_z"], row["z_below_noise"] = bar, bool(abs(row["z"]) <= bar)
            cells_out[s][cid] = dict(stats=cs, row={k: v for k, v in row.items() if k != "mask"}, sharpe_noise=sn, label=cl.cell_label(sp))
            say("   %-10s %-7s n %4d Σ %+7.1f $/сд %+.3f Sh %5.2f | Δ %+.3f ± %.3f z %+.2f p %.4f ΔΣ %+.1f ± %.1f | исключено %d | допуск %s g1 %s g2 %s g3 %s g4 %s g5 %s%s%s" % (
                cid, "пробная" if sp["trial"] else "сосед", cs["n"], cs["sum"], cs["mean"], cs["sharpe"], row["d"], row["se"], row["z"], row["p"], row["dsum"], row["se_sum"],
                row["n_ex"], row["admitted"], row.get("g1"), row.get("g2"), row.get("g3"), row.get("g4"), row.get("g5"), " ПРОШЛА" if row.get("pass") else "",
                " (|z| ≤ планки шума)" if row["z_below_noise"] else ""))
    res_h = {}
    for s in SIDES:
        res_h[s] = {k: outcome(ev["rows"][s], v) for k, v in HYP.items()}
        for k, o in res_h[s].items():
            say("ИСХОД %s, гипотеза %s: %s" % (s, k, o))
    hosts = {s: [cid for cid in trial_cids() if (ev["rows"][s].get(cid) or {}).get("admitted") and all(ev["rows"][s][cid].get(g) for g in ("g1", "g2", "g3", "g4"))] for s in SIDES}
    out = dict(stage="final" if final else "a", m=dict(E=M_E, B=M_B), n_run=n_run, noise=bars, base={s: bs[s] for s in SIDES}, families={"%s/%s" % k: v for k, v in fam.items()},
               cells=cells_out, outcomes=res_h, hosts=hosts)
    if final:
        out["passed"] = [(s, cid) for s in SIDES for cid in trial_cids() if (ev["rows"][s].get(cid) or {}).get("pass")]
    r4.write_json(os.path.join(ROOT, "res", "select68%s.json" % ("" if final else "-a")), r33.jsonable(out))
    if not final:
        r4.write_json(os.path.join(ROOT, "res", "stageA68.json"), dict(hosts=hosts))
        say("стадия A: к (g1)–(g4) прошли %s → соседи считаются только для них" % {s: hosts[s] for s in SIDES})
    else:
        say("прошли (g1)–(g5): %s" % (out["passed"] or "ни одна"))


# ---- справочно на L_n24 ------------------------------------------------------------------------------------------------------------

def cmd_ref(a):
    need_pass()
    u = r.uni()
    bser, bk, bp = load_cell(u, "L", "ref", "REF")
    bst = p8.cell_stats(bser, bk, bp)
    say("L_n24 (REF, = TK-638 байт в байт): n %d, Σ %+.1f $, %+.3f $/сделку, Sharpe %.2f, R:R %.2f" % (bst["n"], bst["sum"], bst["mean"], bst["sharpe"], bst["rr"]["rr"]))
    out = dict(ref=bst, cells={})
    for cid in REF_CELLS:
        ser, kinds, pnl = load_cell(u, "L", "ref", "N24_" + cid)
        cs = p8.cell_stats(ser, kinds, pnl)
        d = k2.delta(ser, bser)
        out["cells"][cid] = dict(stats=cs, delta={k: d[k] for k in ("dsum", "se_sum", "dmean", "se_mean", "z", "p2")})
        say("   N24 И %-6s n %3d Σ %+7.1f $/сд %+.3f Sh %5.2f | ΔΣ к L_n24 %+.1f ± %.1f z %+.2f (справочно, без Холма)" % (cid, cs["n"], cs["sum"], cs["mean"], cs["sharpe"], d["dsum"], d["se_sum"], d["z"]))
    r4.write_json(os.path.join(ROOT, "res", "ref68.json"), r33.jsonable(out))


# ---- ступень 1 ----------------------------------------------------------------------------------------------------------------------

def slice_full(ser, kinds, pnl, m):
    x = r34.slice_plus(ser.day[m], ser.val[m])
    if x["n"]:
        y = rr.rr_stats([dict(kind=k, pnl=p) for k, p in zip(np.asarray(kinds, dtype=object)[m], pnl[m])])
        x["rr"] = dict(rr=y["rr"], mean_win=y["mean_win"], mean_loss=y["mean_loss"], stops=y["stops"]["n"], stop_loss_pct=y["stops"]["mean_loss_pct"])
    else:
        x["rr"] = None
    return x


def tercile(v, edges):
    b = np.full(len(v), 3, dtype=np.int64)
    ok = np.isfinite(v)
    b[ok] = np.searchsorted(np.asarray(edges), v[ok], side="left")
    return b


R_BINS = (("< 0,5", 0.0, 0.5), ("0,5–1", 0.5, 1.0), ("1–2", 1.0, 2.0), ("2–4", 2.0, 4.0), ("> 4", 4.0, np.inf))


def cmd_step1(a):
    need_pass()
    u = r.uni()
    res = {}
    for side in SIDES:
        ser, kinds, pnl = load_base(u, side)
        v = aligned_values(u, side, ser)
        allm = np.ones(len(ser), dtype=bool)
        out = dict(base=slice_full(ser, kinds, pnl, allm), natr_edges=None, cross={}, terc={})
        nb = v["natr"] * 1e4
        e_n = [float(x) for x in np.quantile(nb[np.isfinite(nb)], [1 / 3, 2 / 3])]
        out["natr_edges"] = e_n
        tn = tercile(nb, e_n)
        R = v["R"]
        rb = {nm: np.isfinite(R) & (R >= lo) & (R < hi) for nm, lo, hi in R_BINS}
        rb["нет следующего"] = np.isposinf(R)
        rb["нет значения"] = np.isnan(R)
        for k, nm_k in enumerate(("NATR T1 (низкая)", "NATR T2", "NATR T3 (высокая)")):
            out["cross"][nm_k] = {nm: slice_full(ser, kinds, pnl, (tn == k) & m) for nm, m in rb.items()}
        out["cross"]["все NATR"] = {nm: slice_full(ser, kinds, pnl, m) for nm, m in rb.items()}
        for col in cl.EP_COLS:
            x = v[col]
            edges = [float(q) for q in np.quantile(x[np.isfinite(x)], [1 / 3, 2 / 3])]
            ti = tercile(x, edges)
            out["terc"][col] = dict(edges=edges, rows={nm: slice_full(ser, kinds, pnl, ti == i) for i, nm in enumerate(("T1 (низ)", "T2", "T3 (верх)", "нет значения")) if (ti == i).any()})
        res[side] = out
        b = out["base"]
        say("== ступень 1 %s: сделок %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f, R:R %.2f" % (side, b["n"], b["sum"], b["mean"], b["sharpe"], b["rr"]["rr"]))
        for k, d in out["cross"].items():
            say("   %-18s %s" % (k, "; ".join("%s: %d сд. %+.2f $/сд. Σ %+.0f" % (nm, x["n"], x["mean"], x["sum"]) for nm, x in d.items() if x["n"])))
        for c, d in out["terc"].items():
            say("   %-4s (края %s): %s" % (c, ["%.4g" % x for x in d["edges"]], "; ".join("%s: %d сд. %+.2f $/сд. Σ %+.0f Sh %.2f" % (nm, x["n"], x["mean"], x["sum"], x["sharpe"]) for nm, x in d["rows"].items())))
    r4.write_json(os.path.join(ROOT, "res", "step1-68.json"), r33.jsonable(res))


# ---- E8 -----------------------------------------------------------------------------------------------------------------------------

HOUR = 3_600_000
E8_RULES = {"main": dict(kind=0, par=0.002, tk=1.0, time=HOUR), "stop05": dict(kind=1, par=0.5, tk=1.0, time=HOUR), "stop2": dict(kind=1, par=2.0, tk=1.0, time=HOUR),
            "take2": dict(kind=0, par=0.002, tk=2.0, time=HOUR), "time30": dict(kind=0, par=0.002, tk=1.0, time=HOUR // 2), "time120": dict(kind=0, par=0.002, tk=1.0, time=2 * HOUR)}
KIND_NAME = {ft.K_TAKE: "take", ft.K_STOP: "stop", ft.K_TIME: "time", ft.K_EOD: "eod"}


def e8_replay(u):
    ev = r.e8_events(u)
    rows = {k: [] for k in E8_RULES}
    info = dict(events=len(ev), cut_by_isolation=0, no_tape=0)
    for e in ev:
        t, p, found = cl.load_tape(r.tape_dir(u, e["w"], e["sym"]), e["sym"], e["T_abs"])
        if found:
            keep = t < st.LAST_MS
            if not keep.all():
                info["cut_by_isolation"] += 1
                t, p = t[keep], p[keep]
        for k, ru in E8_RULES.items():
            code, ems, xms, ent, xp, kind, amb = ft.sim_e8(t, p, int(e["T_abs"]), float(e["lp"]), float(e["natr"]), ru["kind"], ru["par"], ru["tk"], ru["time"])
            g, f = ft.e8_money(code, ent, xp, kind)
            if code == ft.CODE_OK:
                day = int((ems - D0_MS) // DAY_MS)
                if not 0 <= day < st.NDAYS:
                    raise GuardError("E8 %s: сутки входа %d вне 0…%d" % (e["sym"], day, st.NDAYS - 1))
            else:
                day = -1
            rows[k].append(dict(sym=e["sym"], w=e["w"], code=int(code), entry=int(ems), exit=int(xms), e=float(ent), xp=float(xp), kind=KIND_NAME.get(int(kind), "?"), amb=int(amb), day=day, pnl=float(g - f), base_neg=bool(e["base_neg"])))
    return rows, info


def line_tests(day, val, sym, m_line=M_L, nboot=st.NBOOT):
    """Ворота линии (§13.68 п. 6): H0 «среднее суточное $ ≤ 0» (перцентильный бутстреп, блок 4), (l2) знак по информативным месяцам (≥ 8 сделок), (l3) без двух лучших суток / монет, (l4) ≥ 70 сделок."""
    day = np.asarray(day, dtype=np.int64)
    val = np.asarray(val, dtype=float)
    Sd = np.bincount(day, weights=val, minlength=st.NDAYS)
    Nd = np.bincount(day, minlength=st.NDAYS).astype(float)
    idx = st.block_idx(st.NDAYS, np.random.default_rng(st.seed_of(Sd, Nd, Sd, Nd)), nboot)
    bm = Sd[idx].mean(axis=1)
    mean = float(Sd.mean())
    se = float(bm.std(ddof=1))
    p = float((1 + int((bm <= 0).sum())) / (1 + len(bm)))
    mon = st.MONTH_OF_DAY[day]
    months = [(int((mon == k).sum()), float(val[mon == k].sum())) for k in range(len(st.MONTH_LEN))]
    inf = [k for k, (n, _) in enumerate(months) if n >= 8]
    need = st.need_months(len(inf))
    pos = sum(1 for k in inf if months[k][1] > 0)
    top_d = np.sort(Sd)[::-1][:2]
    no_days = float(val.sum() - top_d[top_d > 0].sum())
    coins = {}
    for s_, v_ in zip(sym, val):
        coins[s_] = coins.get(s_, 0.0) + float(v_)
    top_c = sorted(coins.values(), reverse=True)[:2]
    no_coins = float(val.sum() - sum(x for x in top_c if x > 0))
    return dict(mean_day=mean, se_day=se, z=mean / se if se > 0 else float("nan"), p=p, holm_first_step=ALPHA / m_line, l1_first_step=bool(p <= ALPHA / m_line), months=months,
                informative_months=inf, need=need, positive_informative=pos, l2=bool(need is not None and pos >= need), no_top2_days=no_days, no_top2_coins=no_coins,
                l3=bool(no_days > 0 and no_coins > 0), l4=bool(len(day) >= 70))


def max_concurrent(iv):
    ev = sorted([(a, 1) for a, _ in iv] + [(b, -1) for _, b in iv], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def cmd_e8(a):
    need_pass()
    u = r.uni()
    rows, info = e8_replay(u)
    sser, skinds, spnl = load_base(u, "S")
    res = dict(info=info, rules={}, sbase=dict(n=len(sser), sum=float(sser.val.sum())))
    wmap = {w.name: w for w in st.TRADE_WINDOWS}
    base_iv = [(wmap[x["w"]].t0_ms + x["entry_ms"], wmap[x["w"]].t0_ms + x["exit_ms"]) for x in r.base_trades(u, "S")]
    for k, rs in rows.items():
        ok = [x for x in rs if x["code"] == ft.CODE_OK]
        day = np.array([x["day"] for x in ok], dtype=np.int64)
        val = np.array([x["pnl"] for x in ok], dtype=float)
        kinds = [x["kind"] for x in ok]
        sl = s5.slice_stats(day, val)
        y = rr.rr_stats([dict(kind=kk, pnl=pp) for kk, pp in zip(kinds, val)])
        Sd = np.bincount(day, weights=val, minlength=st.NDAYS)
        Sb = np.bincount(sser.day, weights=sser.val, minlength=st.NDAYS)
        e_iv = [(x["entry"], x["exit"]) for x in ok]
        res["rules"][k] = dict(params=E8_RULES[k], n_events=len(rs), n=len(ok), not_filled=sum(1 for x in rs if x["code"] == ft.CODE_E_LE_S), no_tape=sum(1 for x in rs if x["code"] == ft.CODE_NOTAPE),
                               eod=sum(1 for x in ok if x["kind"] == "eod"), amb=sum(x["amb"] for x in ok), slice=sl, rr=dict(rr=y["rr"], mean_win=y["mean_win"], mean_loss=y["mean_loss"], kinds=y["kinds"], stops=y["stops"]),
                               maxdd=float((np.maximum.accumulate(np.concatenate([[0.0], np.cumsum(Sd)])) - np.concatenate([[0.0], np.cumsum(Sd)])).max()),
                               with_sbase=dict(sum=float(Sb.sum() + Sd.sum()), sharpe=s5.sharpe_year(Sb + Sd), sharpe_sbase=s5.sharpe_year(Sb), max_concurrent=max_concurrent(base_iv + e_iv), max_concurrent_e8=max_concurrent(e_iv)),
                               trades_per_day_max=int(np.bincount(day, minlength=st.NDAYS).max()) if len(day) else 0, days_with_trades=int((np.bincount(day, minlength=st.NDAYS) > 0).sum()) if len(day) else 0)
        if k == "main":
            res["rules"][k]["line"] = line_tests(day, val, [x["sym"] for x in ok])
            res["rules"][k]["split_base_sign"] = dict(neg=slice_split(ok, True), nonneg=slice_split(ok, False))
    bar = noise_bar(len(E8_RULES))
    res["noise_bar_z"], res["noise_bar_p95"] = bar
    m = res["rules"]["main"]
    ln = m["line"]
    ln["l5"] = bool(abs(ln["z"]) > bar[0])
    ln["passes_l1_l5"] = bool(ln["l1_first_step"] and ln["l2"] and ln["l3"] and ln["l4"] and ln["l5"])
    y = m["rr"]
    say("R:R E8 (основное правило): %.2f (средний выигрыш %+.2f $, средний убыток %+.2f $); стопов %d, убыток стопа средний %+.2f $ (%.2f %% маржи 250 $), макс. %+.2f $ (%.2f %% маржи); ставка 500 $" % (
        y["rr"], y["mean_win"], y["mean_loss"], y["stops"]["n"], y["stops"]["mean_loss"], y["stops"]["mean_loss_pct"], y["stops"]["max_loss"], y["stops"]["max_loss_pct"]))
    say("E8: событий %d, лента обрезана изоляцией у %d" % (info["events"], info["cut_by_isolation"]))
    for k, x in res["rules"].items():
        s_ = x["slice"]
        say("   %-8s n %3d (не исполнена %d, без ленты %d, закрыта концом данных %d, спорных %d): Σ %+8.1f $, %+.3f $/сделку, Sharpe %.2f, месяцы %s, 90 %% [%s; %s]; «база + E8»: Σ %+.1f, Sharpe %.2f (база %.2f), макс. одновр. %d (E8 %d)" % (
            k, x["n"], x["not_filled"], x["no_tape"], x["eod"], x["amb"], s_["sum"], s_["mean"], s_["sharpe"], [(n, round(v)) for n, v in s_["months"]],
            "%.2f" % s_["lo90"] if s_["lo90"] is not None else "—", "%.2f" % s_["hi90"] if s_["hi90"] is not None else "—", x["with_sbase"]["sum"], x["with_sbase"]["sharpe"],
            x["with_sbase"]["sharpe_sbase"], x["with_sbase"]["max_concurrent"], x["with_sbase"]["max_concurrent_e8"]))
    say("линия E8: среднее суточное %+.3f $ ± %.3f (z %+.2f), p %.4f (первый шаг Холма m_L = %d: %.5f) → l1 %s; l2 %s (информативных месяцев %d, положительных %d, нужно %s); l3 %s (без 2 суток %+.1f $, без 2 монет %+.1f $); l4 %s; l5 %s (планка шума %.2f из %d клеток) → %s" % (
        ln["mean_day"], ln["se_day"], ln["z"], ln["p"], M_L, ln["holm_first_step"], ln["l1_first_step"], ln["l2"], len(ln["informative_months"]), ln["positive_informative"], ln["need"], ln["l3"],
        ln["no_top2_days"], ln["no_top2_coins"], ln["l4"], ln["l5"], bar[0], len(E8_RULES), "ПРОШЛА (l1)–(l5)" if ln["passes_l1_l5"] else "не прошла"))
    r4.write_json(os.path.join(ROOT, "res", "e8-68.json"), r33.jsonable(res))
    with open(os.path.join(ROOT, "res", "e8-68-trades.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("rule,sym,w,code,entry_ms,exit_ms,e,xp,kind,amb,day,pnl,s_base_neg\n")
        for k, rs in rows.items():
            for x in rs:
                f.write("%s,%s,%s,%d,%d,%d,%.10g,%.10g,%s,%d,%d,%.6f,%d\n" % (k, x["sym"], x["w"], x["code"], x["entry"], x["exit"], x["e"], x["xp"], x["kind"], x["amb"], x["day"], x["pnl"], int(x["base_neg"])))


def slice_split(ok, neg):
    """Описательно: события, у которых сделка базы (`rt`) была с отрицательным / неотрицательным P&L (188 + 6): n, Σ, $/сделку."""
    v = [x["pnl"] for x in ok if x["base_neg"] == neg]
    return dict(n=len(v), sum=float(sum(v)), mean=float(sum(v) / len(v)) if v else float("nan"))


COMMANDS = {"cells": cmd_cells, "gate": cmd_gate, "select": cmd_select, "ref": cmd_ref, "step1": cmd_step1, "e8": cmd_e8}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
