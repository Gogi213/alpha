#!/usr/bin/env python3
"""П-42 §13.68 (TK-668): банк гипотез, раунд 1 — драйвер счёта на srv. Подкоманды (порядок — §13.68 п. 8 и §13.68.1):
  levels   — прогон базы (P42_LEVELS=1) на окно: дамп уровней всех монет по сторонам → /data/p42-tk668/levels/<окно>/<S|L>_base/<side>-levels.csv (без P&L)
  feat     — признаки R / X24 / X7 / XP / XV / BTC-шок на всех строках дампов сигналов баз → /data/p42-tk668/feat/<окно>-<сторона>.csv (без P&L)
  gate     — G-src, G-n, G-causal, G-off1, G-lvl (без P&L)
  counts   — пороги, клетки, таблицы `--ftab`, E30 (счёт встречных), E8 (события и покрытие ленты), охват (без P&L) → итог для §13.68.2
P&L читают только подкоманды из `tk668_post.py` — после PASS критика на §13.68.2 (файл `/data/p42-tk668/PASS`)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk668_cells as cl  # noqa: E402
import tk668_feat as ft  # noqa: E402

ROOT = "/data/p42-tk668"
UNI = "h775"
SIDES = st.SIDES
SIGN = {"L": 1.0, "S": -1.0}
ENG, VID, BASE = r5.ENG, r5.VID, r5.BASE
say, write_json, sha256 = r4.say, r4.write_json, r4.sha256
GuardError = st.GuardError
TK656_ROOT = "/data/p42-tk656"
FEAT34 = "/data/p42-tk634/feat"


def qq(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def uni():
    return r5.Uni(UNI)


def sigsx_path(u, wname, side):
    return os.path.join(u.base(wname, side), ENG[side] + "-sigsx.csv")


def levels_dir(wname):
    return os.path.join(ROOT, "levels", wname)


def levels_path(wname, side):
    return os.path.join(levels_dir(wname), VID[side], ENG[side] + "-levels.csv")


def cache_path(u, wname, sym):
    return os.path.join(u.wdir(wname), "cache", sym + ".b42")



# ---- чтение входов ------------------------------------------------------------------------------------------------------------------

def read_sigsx(path):
    """Строки дампа сигналов: список словарей (читаем по позициям: 0 sym, 1 level, 2 sig_ms, 3 dist_e8, 4 level_px_e8, 5 born, 6 end, 7 first_bar, 8 natr_e8); дубли ключа (монета, sig_ms) — первая строка."""
    rows, seen, dups = [], set(), 0
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            k = (r[0], int(r[2]))
            if k in seen:
                dups += 1
                continue
            seen.add(k)
            rows.append(dict(sym=r[0], level=int(r[1]), sig_ms=int(r[2]), dist_e8=int(r[3]), px_e8=int(r[4]), born=int(r[5]), end=int(r[6]), first_bar=int(r[7]), natr_e8=int(r[8])))
    return rows, dups


def read_trades_keys(path):
    """Сделки базы без P&L: монета, уровень, сигнал / вход / выход (мс от T0), вид выхода — только эти столбцы."""
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out.append(dict(sym=r["sym"], level=int(r["level"]), sig_ms=int(r["sig_ms"]), entry_ms=int(r["entry_ms"]), exit_ms=int(r["exit_ms"]), kind=r["kind"],
                            base_neg=bool(float(r["gross"]) - float(r["fees"]) < 0)))      # знак P&L самой сделки базы (известный результат TK-662 / 663), не P&L клеток
    return out


def base_trades(u, side):
    """Зачётные сделки базы стороны (дата входа в зачётном диапазоне окна), без P&L: список словарей + окно + сутки 0…135 + месяц."""
    key = ("bt", side)
    if key in _C:
        return _C[key]
    out = []
    for w in st.TRADE_WINDOWS:
        t = read_trades_keys(os.path.join(u.base(w.name, side), ENG[side] + "-trades.csv"))
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for x in t:
            d = int(st.abs_day(w, x["entry_ms"]))
            if lo_i <= d <= hi_i:
                out.append(dict(x, w=w.name, day=d, month=int(st.MONTH_OF_DAY[d])))
    _C[key] = out
    return out


_C = {}
G_TRADES = {"S": 319, "L": 428}                       # TK-662: S_base 319, L_base 428 (зачётные сделки h775)
G_RT = 188                                            # TK-663: выходов `rt` у S_base


# ---- feat --------------------------------------------------------------------------------------------------------------------------

def feat_path(wname, side):
    return os.path.join(ROOT, "feat", "%s-%s.csv" % (wname, side))


def btc_arrays(u, w):
    a = ft.load_b42_full(cache_path(u, w.name, "BTCUSDT"))
    cf = ft.ffill_close(a["o"], a["c"])
    return cf, ft.minute_returns(cf)


def compute_rows(u, w, side, levels_by_sym, cache_dir=None, mins_cache=None):
    """Признаки на строках дампа сигналов окна × стороны: список словарей. `cache_dir` — подмена каталога кэша (тест G-causal на мусоре), `mins_cache` — {монета: Mins} (общий для сторон)."""
    rows, dups = read_sigsx(sigsx_path(u, w.name, side))
    cf, r1 = btc_arrays(u, w)
    s = SIGN[side]
    out = []
    mc = mins_cache if mins_cache is not None else {}
    for x in rows:
        t = x["sig_ms"] // ft.MIN_MS
        natr = x["natr_e8"] / 1e8
        if x["sym"] not in mc:
            mc[x["sym"]] = ft.Mins(ft.load_b42_full(os.path.join(cache_dir or os.path.join(u.wdir(w.name), "cache"), x["sym"] + ".b42")))
        x24, x7, xp, xv = ft.place_features(mc[x["sym"]], t, s, natr)
        sl = levels_by_sym.get(x["sym"])
        if sl is None or x["level"] >= len(sl.born):
            R, pn, own = float("nan"), None, False
        else:
            R, pn, own = ft.room_to_next(sl, x["level"], t, abs(x["px_e8"]), int(s), natr)
        d = dict(x, natr=natr, R=R, X24=x24, X7=x7, XP=xp, XV=xv, own=own, pnext=pn if pn is not None else 0)
        for h in ft.HORIZONS:
            rh, sg = ft.btc_shock(cf, r1, t, h)
            d["r%d" % h], d["sg%d" % h] = rh, sg
            d["bz%d" % h] = s * rh / sg if np.isfinite(rh) and np.isfinite(sg) and sg > 0 else float("nan")
        out.append(d)
    return out, dups


FEAT_COLS = ("level", "natr", "R", "X24", "X7", "XP", "XV", "bz1", "bz5", "bz15", "r1", "r5", "r15", "sg1", "sg5", "sg15", "own", "pnext", "px_e8")


def fmt(v):
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if v != v:
        return ""
    if v in (float("inf"), float("-inf")):
        return "inf" if v > 0 else "-inf"
    return "%.12g" % v


def cmd_feat(a):
    u = uni()
    info = {}
    for w in st.TRADE_WINDOWS:
        mc = {}
        for side in SIDES:
            lv = ft.parse_levels(levels_path(w.name, side))
            rows, dups = compute_rows(u, w, side, lv, mins_cache=mc)
            path = feat_path(w.name, side)
            qq("feat", "x")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(FEAT_COLS) + "\n")
                for x in rows:
                    f.write("%s,%d,%s\n" % (x["sym"], x["sig_ms"], ",".join(fmt(x[c]) for c in FEAT_COLS)))
            nan = {c: int(sum(1 for x in rows if x[c] != x[c])) for c in ("R", "X24", "X7", "XP", "XV", "bz1", "bz5", "bz15")}
            info["%s-%s" % (w.name, side)] = dict(rows=len(rows), dups=dups, nan=nan, sha256=sha256(path))
            say("feat %s-%s: %d строк, дублей ключа %d, пусто %s" % (w.name, side, len(rows), dups, nan))
    write_json(qq("feat", "info.json"), info)


def load_feat(wname, side):
    """{столбец: массив} + ключи из `feat/<окно>-<сторона>.csv`."""
    import pandas as pd
    d = pd.read_csv(feat_path(wname, side), dtype={"sym": str})
    out = {c: d[c].to_numpy(np.float64) for c in FEAT_COLS}
    out["sym"] = d["sym"].to_numpy(object)
    out["sig_ms"] = d["sig_ms"].to_numpy(np.int64)
    return out


# ---- значения на сделках, пороги, события -----------------------------------------------------------------------------------------------

def trade_feat(u, side):
    """Значения признаков на зачётных сделках базы стороны (порядок `base_trades`) + таблицы окон; охрана: каждая сделка находит строку признаков."""
    key = ("tf", side)
    if key in _C:
        return _C[key]
    tr = base_trades(u, side)
    tabs = {w.name: load_feat(w.name, side) for w in st.TRADE_WINDOWS}
    idx = {w: {(s_, int(m_)): i for i, (s_, m_) in enumerate(zip(t["sym"], t["sig_ms"]))} for w, t in tabs.items()}
    cols = {c: np.full(len(tr), np.nan) for c in FEAT_COLS}
    for j, x in enumerate(tr):
        i = idx[x["w"]].get((x["sym"], x["sig_ms"]))
        if i is None:
            raise GuardError("%s: сделка базы %s %d без строки признаков" % (side, x["sym"], x["sig_ms"]))
        for c in FEAT_COLS:
            cols[c][j] = tabs[x["w"]][c][i]
    _C[key] = (cols, tabs)
    return _C[key]


def in_range_mask(tab, w):
    d = st.abs_day(w, tab["sig_ms"])
    return (d >= (w.lod - st.D0).days) & (d <= (w.hid - st.D0).days)


def ftab_path(wname, side):
    return os.path.join(ROOT, "ftab", "%s-%s.csv" % (wname, side))


def opposite_counts(u, margin_ms=0):
    """E30: по сделкам базы стороны — сколько сделок имеют ≥ 1 встречный сигнал (строка дампа сигналов другой стороны той же монеты, `sig_ms ∈ (entry_ms − margin, exit_ms + margin]`, margin = 0 — запись §13.68) и сколько всего таких сигналов; по месяцам."""
    out = {}
    other = {"S": "L", "L": "S"}
    for side in SIDES:
        sig = {}
        for w in st.TRADE_WINDOWS:
            rows, _ = read_sigsx(sigsx_path(u, w.name, other[side]))
            for x in rows:
                sig.setdefault((w.name, x["sym"]), []).append(x["sig_ms"])
        tr = base_trades(u, side)
        n_tr, n_sig, by_month = 0, 0, [0] * len(st.MONTH_LEN)
        for x in tr:
            hit = [m for m in sig.get((x["w"], x["sym"]), []) if x["entry_ms"] - margin_ms < m <= x["exit_ms"] + margin_ms]
            if hit:
                n_tr += 1
                n_sig += len(hit)
                by_month[x["month"]] += 1
        out[side] = dict(n_base=len(tr), n30=n_tr, n_signals=n_sig, by_month=by_month, drop=bool(n_tr < cl.MIN_N))
    return out


def tape_dir(u, wname, sym):
    return os.path.join(u.wdir(wname), "aggtrades", sym)


def e8_events(u):
    """События E8: зачётные сделки `S_base` с `kind = rt` + уровень (`|level_px_e8|`), NATR из дампа сигналов; список словарей."""
    sig = {}
    for w in st.TRADE_WINDOWS:
        rows, _ = read_sigsx(sigsx_path(u, w.name, "S"))
        for x in rows:
            sig[(w.name, x["sym"], x["sig_ms"])] = x
    out = []
    for x in base_trades(u, "S"):
        if x["kind"] != "rt":
            continue
        s = sig.get((x["w"], x["sym"], x["sig_ms"]))
        if s is None:
            raise GuardError("E8: сделка %s %d без строки дампа сигналов" % (x["sym"], x["sig_ms"]))
        w = next(w for w in st.TRADE_WINDOWS if w.name == x["w"])
        out.append(dict(x, lp=abs(s["px_e8"]) / 1e8, natr=s["natr_e8"] / 1e8, T_abs=w.t0_ms + x["exit_ms"] + 4, t0_ms=w.t0_ms, px_neg=bool(s["px_e8"] < 0)))
    return out


# ---- counts ------------------------------------------------------------------------------------------------------------------------

def cmd_counts(a):
    """Без P&L: пороги, клетки (охват, исключено по месяцам, допуск, дубли, Jaccard), таблицы `--ftab`, корреляции с `mv5` / `mv60`, E30 (n30), E8 (события, лента, e ≤ S)."""
    import pandas as pd
    u = uni()
    for side in SIDES:
        cols, tabs = trade_feat(u, side)
        tr = base_trades(u, side)
        mon = np.array([x["month"] for x in tr])
        n_t = len(tr)
        if n_t != G_TRADES[side]:
            raise GuardError("G-n: сделок базы %s %d ≠ %d (TK-662)" % (side, n_t, G_TRADES[side]))
        thr = cl.quantile_table(cols)
        specs = cl.cell_specs()
        res = dict(side=side, n_trades=n_t, thr=thr, cells=[], jaccard={}, coverage={}, corr={})
        res["base_by_month"] = [int((mon == k).sum()) for k in range(len(st.MONTH_LEN))]
        for c in ("R", "X24", "X7", "XP", "XV", "bz1", "bz5", "bz15"):
            v = cols[c]
            res["coverage"][c] = [round(float((v[mon == k] == v[mon == k]).mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
        res["R_dist"] = {"нет следующего": int(np.isinf(cols["R"]).sum())}
        for lo, hi in ((0, 0.5), (0.5, 1), (1, 2), (2, 4), (4, float("inf"))):
            res["R_dist"]["%g–%g" % (lo, hi)] = int(((cols["R"] >= lo) & (cols["R"] < hi) & np.isfinite(cols["R"])).sum())
        for h in ft.HORIZONS:
            z = cols["bz%d" % h]
            zz = z[z == z]
            res["corr"]["shock_share_%d" % h] = dict(abs_ge2=float((np.abs(zz) >= 2).mean()), by_trade=float((zz >= 2).mean()), against=float((zz <= -2).mean()), n=int(len(zz)))
        f34 = {}
        for w in st.TRADE_WINDOWS:
            d34 = pd.read_csv(os.path.join(FEAT34, UNI, "%s-%s.csv" % (w.name, side)), usecols=["sym", "sig_ms", "mv5", "mv60", "natr_bp"], dtype={"sym": str})
            for s_, m_, a5, a60, nb in zip(d34["sym"], d34["sig_ms"], d34["mv5"], d34["mv60"], d34["natr_bp"]):
                f34[(w.name, s_, int(m_))] = (a5, a60, nb)
        nan3 = (np.nan,) * 3
        mv5 = np.array([f34.get((x["w"], x["sym"], x["sig_ms"]), nan3)[0] for x in tr])
        mv60 = np.array([f34.get((x["w"], x["sym"], x["sig_ms"]), nan3)[1] for x in tr])
        nbp = np.array([f34.get((x["w"], x["sym"], x["sig_ms"]), nan3)[2] for x in tr])
        res["corr"]["tk634_missing"] = int(np.isnan(mv5).sum())
        for c in cl.EP_COLS:
            res["corr"][c] = dict(mv60=cl.spearman(cols[c], mv60), natr_bp=cl.spearman(cols[c], nbp))
        for h in ft.HORIZONS:
            rh = cols["r%d" % h]
            res["corr"]["btc%d" % h] = dict(rh_s_vs_mv5=cl.spearman(rh * SIGN[side], mv5), abs_rh_vs_abs_mv5=cl.spearman(np.abs(rh), np.abs(mv5)))
        masks, seen = {}, {}
        for sp in specs:
            mk = cl.excl_mask(sp, cols[sp["col"]], thr)
            masks[sp["cid"]] = mk
            dup = seen.setdefault(np.packbits(mk).tobytes(), sp["cid"])
            dup = None if dup == sp["cid"] else dup
            rem = [int(((mon == k) & ~mk).sum()) for k in range(len(st.MONTH_LEN))]
            ex_c = n_c = 0
            for w in st.TRADE_WINDOWS:
                sc = in_range_mask(tabs[w.name], w)
                ex_c += int((cl.excl_mask(sp, tabs[w.name][sp["col"]], thr) & sc).sum())
                n_c += int(sc.sum())
            subset, admit = cl.admit_floor(rem, int(mk.sum()), n_t)
            res["cells"].append(dict(cid=sp["cid"], fam=sp["fam"], trial=sp["trial"], group=sp["group"], label=cl.cell_label(sp), dup_of=dup, E=int(mk.sum()),
                                     excl_by_month=[int((mk & (mon == k)).sum()) for k in range(len(st.MONTH_LEN))], remain_by_month=rem, subset=subset, admit_floor=admit, cand_excluded=ex_c, cand_rows=n_c))
        for sp in specs:
            if sp["trial"]:
                res["jaccard"][sp["cid"]] = {e["cid"]: round(st.jaccard(masks[sp["cid"]], masks[e["cid"]]), 3) for e in specs if not e["trial"] and e["group"] == sp["cid"]}
        trial = [c for c in res["cells"] if c["trial"]]
        res["m_trial_nodup"] = sum(1 for c in trial if c["dup_of"] is None)
        res["m_trial_admit"] = sum(1 for c in trial if c["admit_floor"])
        write_json(qq("res", "counts68-%s.json" % side), res)
        say("== %s: сделок базы %d (по месяцам %s); пробных клеток %d, без дублей %d, с допуском %d" % (side, n_t, res["base_by_month"], len(trial), res["m_trial_nodup"], res["m_trial_admit"]))
        say("   R на сделках: %s" % res["R_dist"])
        say("   охват по месяцам: %s" % res["coverage"])
        say("   пороги EP: %s" % {c: {q: round(x, 4) for q, x in v.items()} for c, v in thr.items()})
        for c in res["cells"]:
            say("   %-14s %-5s E=%4d (%.1f %%) по месяцам %s, останется %s, подмножество %s, допуск %s%s" % (c["cid"], "проб." if c["trial"] else "сосед", c["E"], 100 * c["E"] / n_t, c["excl_by_month"], c["remain_by_month"], c["subset"], c["admit_floor"], " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
        for k, v in res["corr"].items():
            say("   корр. %s: %s" % (k, v))
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            cc = {"ALL1": np.ones(len(t["sym"]))}
            cc.update({sp["cid"]: np.where(cl.excl_mask(sp, t[sp["col"]], thr), 0.0, 1.0) for sp in specs})
            path = qq("ftab", "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cc) + "\n")
                for i in range(len(t["sym"])):
                    f.write("%s,%d,%s\n" % (t["sym"][i], int(t["sig_ms"][i]), ",".join("%.1f" % cc[k][i] for k in cc)))
            say("ftab %s-%s: %d строк, sha256 %s" % (w.name, side, len(t["sym"]), sha256(path)[:16]))
    o30 = opposite_counts(u)
    write_json(qq("res", "counts68-e30.json"), o30)
    for s, v in o30.items():
        say("E30 %s: сделок базы %d, с встречным сигналом %d (порог 70), встречных сигналов %d, по месяцам %s → %s" % (s, v["n_base"], v["n30"], v["n_signals"], v["by_month"], "DROP" if v["drop"] else "считаем"))
    ev = e8_events(u)
    cov = dict(events=len(ev), expected_tk663=G_RT, rt_base_neg=sum(1 for e in ev if e["base_neg"]), by_month=[0] * len(st.MONTH_LEN), px_neg=sum(1 for e in ev if e["px_neg"]), no_tape=0, only_one_day=0, e_le_s=0)
    if len(ev) != G_RT:
        say("G-n E8: событий rt %d ≠ %d (оценка TK-663): разбор — rt с отрицательным P&L самой сделки базы %d, с неотрицательным %d; правило «все rt» не меняется — на решение критика" % (
            len(ev), G_RT, cov["rt_base_neg"], len(ev) - cov["rt_base_neg"]))
    for e in ev:
        cov["by_month"][e["month"]] += 1
        t, p, found = cl.load_tape(tape_dir(u, e["w"], e["sym"]), e["sym"], e["T_abs"])
        i0 = int(np.searchsorted(t, e["T_abs"])) if found else 0
        if found == 0 or i0 >= len(t):
            cov["no_tape"] += 1
            continue
        cov["only_one_day"] += int(found == 1)
        cov["e_le_s"] += int(p[i0] * (1 + ft.SLIP) <= e["lp"] * (1 - 0.002))
    write_json(qq("res", "counts68-e8.json"), cov)
    say("E8: событий %d (по месяцам %s), цена уровня шорта < 0: %d, без ленты %d, один день ленты %d, «не исполнена» (e ≤ S) %d" % (cov["events"], cov["by_month"], cov["px_neg"], cov["no_tape"], cov["only_one_day"], cov["e_le_s"]))
    say("counts готово — §13.68.2 до чтения P&L")


def cmd_e30wide(a):
    """Проверка стыка двух дампов для E30 (замечание критика к §13.68.2, не ворота): счёт встречных с окном (вход − 24 ч, выход + 24 ч) рядом со счётом по записи; без P&L."""
    u = uni()
    day = 86_400_000
    out = dict(exact=opposite_counts(u), wide24=opposite_counts(u, day))
    write_json(qq("res", "counts68-e30wide.json"), out)
    for side in SIDES:
        e, w = out["exact"][side], out["wide24"][side]
        say("E30 %s: сделок базы %d; встречный сигнал в [вход, выход]: сделок %d, сигналов %d; в (вход − 24 ч, выход + 24 ч]: сделок %d, сигналов %d, по месяцам %s" % (side, e["n_base"], e["n30"], e["n_signals"], w["n30"], w["n_signals"], w["by_month"]))


# ---- levels ----------------------------------------------------------------------------------------------------------------------

def cmd_levels(a):
    """Один прогон базы (обе стороны) на окно с `P42_LEVELS=1`: дамп уровней. Сделки прогона сверяются с готовой базой побайтно (ворота G-lvl-base)."""
    u = uni()
    bad = []
    for w in st.TRADE_WINDOWS:
        out = levels_dir(w.name)
        if not os.path.exists(out + "/DONE"):
            st.check_dates(w.t0, w.hi)
            r5.tick(u, w, [(VID[s], BASE[s] + u.extra) for s in SIDES], out, {"P42_LEVELS": "1"}, qq("log", "levels-%s.log" % w.name))
            open(out + "/DONE", "w").close()
        for s in SIDES:
            for fn in ("trades", "daily", "coinday", "entryrej"):
                x = os.path.join(out, VID[s], "%s-%s.csv" % (ENG[s], fn))
                y = os.path.join(u.base(w.name, s), "%s-%s.csv" % (ENG[s], fn))
                if not (os.path.exists(x) and os.path.exists(y) and open(x, "rb").read() == open(y, "rb").read()):
                    bad.append("%s %s %s" % (w.name, s, fn))
    say("levels:", "FAIL (прогон с дампом ≠ базе) " + "; ".join(bad) if bad else "ok: 4 файла × 2 стороны × 3 окна = базе байт в байт")
    if bad:
        sys.exit(1)


COMMANDS = {"levels": cmd_levels, "feat": cmd_feat, "counts": cmd_counts, "e30wide": cmd_e30wide}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--k", type=int, default=300)
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
