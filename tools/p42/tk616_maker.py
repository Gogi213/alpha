#!/usr/bin/env python3
"""TK-616 / П-42 §13.28 п. 5–6: пять месяцев мейкера (клетки L_mk1, S_mk1, L_mk2, S_mk2) на окнах wa/wb/wc, помесячно, гейт G_mk, диагностика «выбор по сигналам».
Запускать на srv под `systemd-run` (не с ПК). Код — из рабочего дерева ветки; драйвер движка и статистика — TK-599 (`retitr_full_run.Engine`, `retitr_full`).

  python3 tk616_maker.py plan                            # клетки, vid, команды движка — без запуска
  TK616_GO=1 python3 tk616_maker.py run --bin <alpha-mk>    # четыре клетки на wa/wb/wc + ворота швов (G3) для мейкер-клеток
  python3 tk616_maker.py report [--nboot 100000] [--md файл] # таблицы по месяцам, G_mk, диагностика; results.json

Корень счёта — /data/p42-tk616/root (ссылки на окна, склад сделок и кэш сигналов TK-599; свои out/, var/, log/ — прогон TK-599 не затрагивается).
Тейкер-базы стороны — готовые выходы TK-599 (/data/p42-tk599/out/<окно>/<vid базы>; допустимы после записи `guard.py equiv` a112684a ≡ a03c91df).
Метрика — средний $ на сделку = ΣS/ΣN (gross − fees, без фандинга); медиан и t-статистик по сделкам нет; единица статистики — сутки.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402

BASE_ROOT = "/data/p42-tk599"
ROOT = "/data/p42-tk616/root"
OUT_DIR = "/data/p42-tk616/results"
GO_ENV = "TK616_GO"
NBOOT = 100_000                                     # §13.28 п. 5: ≥ 100 000 повторов
HOLM_ALPHA = 0.05
MIN_MONTH_TRADES = 5                                # g2: месяц учитывается при ≥ 5 сделок
MODES = (1, 2)
SIDE_NAME = {"L": "long", "S": "short"}
WINS = [w.name for w in rf.WINDOWS]


def base_keys(spec, side):
    return rf.cfg_keys(spec, side, {})


def cells(spec):
    """{id клетки: Req}: база стороны + `mk=1|2`; `bases` — тейкер-база стороны (эталон сравнений)."""
    cs, bs = {}, {}
    for s in rf.SIDES:
        bs[s] = rf.Req(s, base_keys(spec, s), False)
        for mk in MODES:
            cs["%s_mk%d" % (s, mk)] = rf.Req(s, "%s mk=%d" % (base_keys(spec, s), mk), False)
    return cs, bs


def prepare_root(root=ROOT, base=BASE_ROOT):
    """Корень счёта: ссылки на данные окон, склад и кэш сигналов TK-599; выходы — свои."""
    os.makedirs(root, exist_ok=True)
    for name in ("wa", "wb", "wc", "tick-store", "sig-cache"):
        p = os.path.join(root, name)
        if not os.path.lexists(p):
            os.symlink(os.path.join(base, name), p)


def go_required(env=None):
    if (env if env is not None else os.environ).get(GO_ENV) != "1":
        raise rf.GuardError("%s=1 не задан: запуск движка запрещён до команды исполнителя (код mk прошёл ворота G-B1)" % GO_ENV)


# ---- чтение выходов -------------------------------------------------------------------------------------------------------------

def out_dir(root, w, vid):
    return os.path.join(root, "out", w, vid)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def tables_of(root, vid, side):
    return {w: rf.read_trades_csv(os.path.join(out_dir(root, w, vid), SIDE_NAME[side] + "-trades.csv")) for w in WINS}


def daily_of(root, vid, side):
    return {w: rf.read_daily_csv(os.path.join(out_dir(root, w, vid), SIDE_NAME[side] + "-daily.csv")) for w in WINS}


def rows_of(root, vid, side, name):
    return {w: read_csv(os.path.join(out_dir(root, w, vid), SIDE_NAME[side] + "-" + name)) for w in WINS}


def zachet(w, entry_ms):
    """Маска зачётного диапазона окна по дате входа (как в `rf.splice`)."""
    d = rf.abs_day(w, entry_ms)
    return (d >= (w.lod - rf.D0).days) & (d <= (w.hid - rf.D0).days)


def trade_keys(rows, w):
    """{(окно, монета, уровень, sig_ms): (месяц, gross − fees)} — зачётные сделки окна (вход по дате в диапазоне)."""
    if not rows:
        return {}
    e = np.array([int(r["entry_ms"]) for r in rows], dtype=np.int64)
    day = rf.abs_day(w, e)
    m = zachet(w, e)
    return {(w.name, r["sym"], r["level"], int(r["sig_ms"])): (int(rf.MONTH_OF_DAY[day[i]]), float(r["gross"]) - float(r["fees"]))
            for i, r in enumerate(rows) if m[i]}


def rej_keys(rows, w):
    """{(окно, монета, уровень, sig_ms): причина} отказов входа окна по зачётным суткам сигнала (месяц — по суткам сигнала)."""
    out = {}
    for r in rows:
        ms = int(r["sig_ms"])
        day = int(rf.abs_day(w, np.array([ms], dtype=np.int64))[0])
        if (w.lod - rf.D0).days <= day <= (w.hid - rf.D0).days:
            out[(w.name, r["sym"], r["level"], ms)] = (r["why"], int(rf.MONTH_OF_DAY[day]))
    return out


# ---- ворота швов окон для мейкер-клеток (как `gate3` TK-599, без окон сходимости) --------------------------------------------------------

def seams(root, vid, side):
    tabs = tables_of(root, vid, side)
    wins = {w.name: w for w in rf.WINDOWS}
    diff = []
    for a, b, lo, hi in rf.G3_RANGES:
        diff += ["%s–%s: %s" % (a, b, x) for x in rf.overlap_diff(tabs[a], wins[a], tabs[b], wins[b], lo, hi)]
    return {"diff": diff, "forced_ok": rf.check_forced(tabs), "last_exit_ms": rf.check_data_end(tabs),
            "n": {w: len(t) for w, t in tabs.items()}}


# ---- статистика --------------------------------------------------------------------------------------------------------------------

def series_of(root, vid, side, coins):
    ser = rf.splice(tables_of(root, vid, side), coins, daily=daily_of(root, vid, side))
    return rf.day_sums(ser)


def month_rows(S, N):
    """По месяцам: сумма, число сделок, среднее на сделку, $/сут. — и по всему окну."""
    out = []
    for m, n in enumerate(rf.MONTH_LEN):
        d = rf.month_days([m])
        s, c = float(S[d].sum()), int(N[d].sum())
        out.append({"month": rf.MONTH_NAMES[m], "days": n, "sum": s, "n": c, "mean": s / c if c else float("nan"), "per_day": s / n})
    s, c = float(S.sum()), int(N.sum())
    out.append({"month": "all", "days": rf.NDAYS, "sum": s, "n": c, "mean": s / c if c else float("nan"), "per_day": s / rf.NDAYS})
    return out


def holm_threshold(ps, alpha=HOLM_ALPHA):
    """Порог Холма для каждой клетки: k-й по возрастанию p против alpha/(m − k + 1); возвращает (отвергнуто, порог по клетке)."""
    m = len(ps)
    order = sorted(range(m), key=lambda j: ps[j])
    thr = [0.0] * m
    for k, i in enumerate(order, start=1):
        thr[i] = alpha / (m - k + 1)
    return rf.holm_reject(ps, alpha), thr


def gmk(cell_stats, base_by_side, nboot=NBOOT):
    """Гейт G_mk (§13.28 п. 6) по клеткам {id: {'side', 'S', 'N'}}; base_by_side — {side: (S, N)} тейкер-базы. Возвращает {id: исход}."""
    ids = sorted(cell_stats)
    own = {i: rf.own_stat(cell_stats[i]["S"], cell_stats[i]["N"], nboot) for i in ids}
    rej, thr = holm_threshold([own[i].p for i in ids])
    res = {}
    for k, i in enumerate(ids):
        c = cell_stats[i]
        Sb, Nb = base_by_side[c["side"]]
        mr = month_rows(c["S"], c["N"])[:-1]
        mb = month_rows(Sb, Nb)[:-1]
        counted = [r for r in mr if r["n"] >= MIN_MONTH_TRADES]
        pos = sum(1 for r in counted if r["mean"] > 0)
        ds = rf.delta_stat(c["S"], c["N"], Sb, Nb, nboot)
        mean_x, mean_b = ds.s_c / ds.n_c, ds.s_r / ds.n_r
        g1 = bool(own[i].total > 0 and rej[k])
        g2 = bool(pos >= 4)
        g3 = bool(mean_x > mean_b and ds.dsum >= -ds.se_sum)
        res[i] = {"side": c["side"], "sum": own[i].total, "n": own[i].n, "mean": own[i].mean, "p": own[i].p, "holm_threshold": thr[k], "holm_reject": bool(rej[k]),
                  "positive_months": pos, "counted_months": len(counted), "mean_base": mean_b, "sum_base": ds.s_r, "n_base": ds.n_r,
                  "delta_mean": ds.d, "delta_sum": ds.dsum, "se_sum": ds.se_sum, "g1": g1, "g2": g2, "g3": g3, "pass": g1 and g2 and g3,
                  "months": mr, "months_base": mb}
    return res


def diagnostics(root, vid_c, vid_b, side):
    """«Выбор по сигналам» (§13.28 п. 5): ключ — окно + монета + уровень + sig_ms. Сигналы мейкера `notick` исключены из обоих множеств.
    Разбор Δ = ΣS(мейкер) − ΣS(тейкер) на эффект исполнения (те же сигналы) и эффект выбора (только у тейкера по причинам / только у мейкера)."""
    wins = {w.name: w for w in rf.WINDOWS}
    C, B, R = {}, {}, {}
    for w in WINS:
        C.update(trade_keys(read_csv(os.path.join(out_dir(root, w, vid_c), SIDE_NAME[side] + "-trades.csv")), wins[w]))
        B.update(trade_keys(read_csv(os.path.join(out_dir(BASE_ROOT, w, vid_b), SIDE_NAME[side] + "-trades.csv")), wins[w]))
        R.update(rej_keys(read_csv(os.path.join(out_dir(root, w, vid_c), SIDE_NAME[side] + "-entryrej.csv")), wins[w]))
    return classify(C, B, R)


def classify(C, B, R):
    """C, B — {ключ: (месяц, P&L)} мейкера и тейкера; R — {ключ: (причина, месяц)} отказов мейкера. Возвращает {месяц: {...}} и 'all'."""
    notick = {k for k, (why, _) in R.items() if why == "notick"}
    nm = len(rf.MONTH_LEN)
    z = lambda: {"both": 0, "both_c": 0.0, "both_b": 0.0, "cell_only": 0, "cell_only_c": 0.0,   # noqa: E731
                 "base_only": {"unfilled": [0, 0.0], "touched": [0, 0.0], "notick": [0, 0.0], "displaced": [0, 0.0]},
                 "rej": {"unfilled": 0, "touched": 0, "notick": 0}}
    out = {m: z() for m in range(nm)}
    for k, (m, v) in C.items():
        if k in B:
            o = out[m]
            o["both"] += 1
            o["both_c"] += v
            o["both_b"] += B[k][1]
        elif k not in notick:
            out[m]["cell_only"] += 1
            out[m]["cell_only_c"] += v
    for k, (m, v) in B.items():
        if k in C:
            continue
        why = R[k][0] if k in R else "displaced"
        why = why if why in ("unfilled", "touched", "notick") else "displaced"
        out[m]["base_only"][why][0] += 1
        out[m]["base_only"][why][1] += v
    for k, (why, m) in R.items():
        if why in out[m]["rej"]:
            out[m]["rej"][why] += 1
    tot = z()
    for m in range(nm):
        o = out[m]
        for key in ("both", "both_c", "both_b", "cell_only", "cell_only_c"):
            tot[key] += o[key]
        for r in ("unfilled", "touched", "notick", "displaced"):
            tot["base_only"][r][0] += o["base_only"][r][0]
            tot["base_only"][r][1] += o["base_only"][r][1]
        for r in o["rej"]:
            tot["rej"][r] += o["rej"][r]
    out["all"] = tot
    for o in out.values():
        o["exec_effect"] = o["both_c"] - o["both_b"]
        o["select_effect"] = o["cell_only_c"] - sum(v[1] for v in o["base_only"].values())
        o["delta"] = o["exec_effect"] + o["select_effect"]
    return out


# ---- отчёт -------------------------------------------------------------------------------------------------------------------------

def f2(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else ("%+." + str(nd) + "f") % x


def render(res, run_info=None):
    L = ["# TK-616 — вход лимиткой (мейкер), пять месяцев (§13.28 п. 5–6)", ""]
    if run_info:
        L += ["Бинарь: `%s` md5 `%s`; окна wa/wb/wc; нарезка `retitr_full.splice`; без фандинга; число клеток m = 4 («найдено на этих данных»).\n" % (run_info.get("bin"), run_info.get("md5"))]
    L += ["## G_mk (Холм m = 4, односторонне 5 %)", "",
          "| клетка | ΣS $ | сделок | $/сделку | p | порог Холма | g1 | мес. > 0 (из учтённых) | g2 | $/сделку тейкер | ΔΣ $ ± SE | g3 | G_mk |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i in sorted(res["gmk"]):
        g = res["gmk"][i]
        L.append("| %s | %s | %d | %s | %.4f | %.4f | %s | %d (%d) | %s | %s | %s ± %.2f | %s | **%s** |" % (
            i, f2(g["sum"]), g["n"], f2(g["mean"], 3), g["p"], g["holm_threshold"], "да" if g["g1"] else "нет", g["positive_months"], g["counted_months"],
            "да" if g["g2"] else "нет", f2(g["mean_base"], 3), f2(g["delta_sum"]), g["se_sum"], "да" if g["g3"] else "нет", "ПРОШЛА" if g["pass"] else "нет"))
    L += ["", "## Помесячно (ΣS $, сделок, $/сделку, $/сут.) — клетка и тейкер-база стороны", ""]
    for i in sorted(res["gmk"]):
        g = res["gmk"][i]
        L += ["### %s" % i, "", "| месяц | сут. | ΣS мейкер | N | $/сделку | $/сут. | ΣS тейкер | N | $/сделку | $/сут. |", "|---|---|---|---|---|---|---|---|---|---|"]
        allr, allb = month_rows_all(g)
        for r, b in zip(allr, allb):
            L.append("| %s | %d | %s | %d | %s | %s | %s | %d | %s | %s |" % (r["month"], r["days"], f2(r["sum"]), r["n"], f2(r["mean"], 3), f2(r["per_day"]),
                                                                          f2(b["sum"]), b["n"], f2(b["mean"], 3), f2(b["per_day"])))
        L.append("")
    L += ["## Заполнение и «выбор по сигналам»", "",
          "Заполнение = сделок / (сделок + unfilled + touched). Сигналы `notick` исключены из множеств «обоих» и «только мейкер»; у тейкера они показаны отдельной строкой. «Вытеснено» — сделка тейкера, которой нет у мейкера без причины в `entryrej` (ход портфеля).", ""]
    for i in sorted(res["diag"]):
        d = res["diag"][i]
        L += ["### %s" % i, "", "| месяц | сделок | unfilled | touched | notick | заполнение | оба: n | оба: ΣS мейкер | оба: ΣS тейкер | эффект исполнения | только тейкер: unfilled n/ΣS | touched n/ΣS | notick n/ΣS | вытеснено n/ΣS | только мейкер n/ΣS | эффект выбора | ΔΣ |", "|" + "---|" * 17]
        for m in list(range(len(rf.MONTH_LEN))) + ["all"]:
            o = d[str(m)]
            n_c = o["both"] + o["cell_only"]
            den = n_c + o["rej"]["unfilled"] + o["rej"]["touched"]
            bo = o["base_only"]
            L.append("| %s | %d | %d | %d | %d | %s | %d | %s | %s | %s | %d/%s | %d/%s | %d/%s | %d/%s | %d/%s | %s | %s |" % (
                "all" if m == "all" else rf.MONTH_NAMES[m], n_c, o["rej"]["unfilled"], o["rej"]["touched"], o["rej"]["notick"],
                ("%.1f%%" % (100.0 * n_c / den)) if den else "—", o["both"], f2(o["both_c"]), f2(o["both_b"]), f2(o["exec_effect"]),
                bo["unfilled"][0], f2(bo["unfilled"][1]), bo["touched"][0], f2(bo["touched"][1]), bo["notick"][0], f2(bo["notick"][1]),
                bo["displaced"][0], f2(bo["displaced"][1]), o["cell_only"], f2(o["cell_only_c"]), f2(o["select_effect"]), f2(o["delta"])))
        L.append("")
    L += ["## Ворота швов окон (G3) и охрана", ""]
    for i in sorted(res["seams"]):
        s = res["seams"][i]
        L.append("- %s: различий на швах %d%s; сделок по окнам %s; forced в конце данных %d" % (i, len(s["diff"]), "" if not s["diff"] else " — " + "; ".join(s["diff"][:3]), s["n"], s["forced_ok"]))
    return "\n".join(L) + "\n"


def month_rows_all(g):
    return g["months"] + [g["all_row"]], g["months_base"] + [g["all_row_base"]]


# ---- команды ---------------------------------------------------------------------------------------------------------------------

def cmd_plan(a):
    spec = rf.Spec.load(rr.GRIDS)
    cs, bs = cells(spec)
    print("корень %s; склад %s; кэш сигналов %s; бинарь %s" % (a.root, os.path.join(a.root, "tick-store"), os.path.join(a.root, "sig-cache"), a.bin or "(не задан)"))
    for s, r in bs.items():
        print("база %s vid %s  %s" % (s, r.vid, r.engine_keys))
    for i, r in cs.items():
        print("клетка %s vid %s  %s" % (i, r.vid, r.engine_keys))
    text, coins = rr.read_symbols(a.symbols)
    print("монет %d" % len(coins))
    return 0


def make_engine(a):
    prepare_root(a.root)
    text, coins = rr.read_symbols(a.symbols)
    paths = rr.Paths(a.root, tick_store=os.path.join(a.root, "tick-store"), sig_cache=os.path.join(a.root, "sig-cache"))
    return paths, text, coins, rr.Engine(paths, rr.SubprocessRunner(), text, a.bin, a.threads, None, prefix="m")


def cmd_run(a):
    go_required()
    if not a.bin or not os.path.exists(a.bin):
        raise rf.GuardError("--bin: нет файла %r" % a.bin)
    spec = rf.Spec.load(rr.GRIDS)
    cs, _ = cells(spec)
    paths, text, coins, engine = make_engine(a)
    sha, md5 = rr.file_hashes(a.bin)
    os.makedirs(OUT_DIR, exist_ok=True)
    info = {"bin": a.bin, "sha256": sha, "md5": md5, "cells": {i: {"vid": r.vid, "keys": r.engine_keys} for i, r in cs.items()}}
    json.dump(info, open(os.path.join(OUT_DIR, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("бинарь %s md5 %s sha256 %s" % (a.bin, md5, sha))
    engine.launch(list(cs.values()))
    seam = {i: seams(a.root, r.vid, r.side) for i, r in cs.items()}
    json.dump(seam, open(os.path.join(OUT_DIR, "seams.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = {i: s["diff"][:5] for i, s in seam.items() if s["diff"]}
    if bad:
        raise rf.GuardError("G3 швов окон, мейкер-клетки: %s" % bad)
    print("прогон закончен; швы окон: различий 0")
    return 0


def cmd_report(a):
    spec = rf.Spec.load(rr.GRIDS)
    cs, bs = cells(spec)
    text, coins = rr.read_symbols(a.symbols)
    cell_stats, base = {}, {}
    for i, r in cs.items():
        S, N = series_of(a.root, r.vid, r.side, coins)
        cell_stats[i] = {"side": r.side, "S": S, "N": N}
    for s, r in bs.items():
        base[s] = series_of(BASE_ROOT, r.vid, r.side, coins)
    res = {"gmk": gmk(cell_stats, base, a.nboot), "diag": {}, "seams": {}}
    for i, r in cs.items():
        g = res["gmk"][i]
        g["all_row"] = month_rows(cell_stats[i]["S"], cell_stats[i]["N"])[-1]
        g["all_row_base"] = month_rows(*base[r.side])[-1]
        res["diag"][i] = {str(k): v for k, v in diagnostics(a.root, r.vid, bs[r.side].vid, r.side).items()}
    sp = os.path.join(OUT_DIR, "seams.json")
    if os.path.exists(sp):
        res["seams"] = json.load(open(sp, encoding="utf-8"))
    info = json.load(open(os.path.join(OUT_DIR, "run.json"), encoding="utf-8")) if os.path.exists(os.path.join(OUT_DIR, "run.json")) else None
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(res, open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    md = render(res, info)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(md)
    print(md)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "run", "report"])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--symbols", default=rr.SYMBOLS)
    ap.add_argument("--threads", type=int, default=rr.THREADS)
    ap.add_argument("--nboot", type=int, default=NBOOT)
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "run": cmd_run, "report": cmd_report}[a.cmd](a)
    except rf.GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
