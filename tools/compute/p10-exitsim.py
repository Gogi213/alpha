#!/usr/bin/env python3
"""П-10 клетки T/U/V (Г-24, Г-25, Г-109) и клетка-база `b1-sim`: пересчёт выходов сделок B1 (Г-85б, `p08-b1`) по минутным
свечам через `exit-sim.py` (импорт: `simulate`, `Bars`, `rv_24h`, семейства E29/E30 — ничего не скопировано).

Вход — принятые закрытия B1 тем же путём, что П-08 (`p08-run.py`/`p07-h9h10.py`): `busy-replay` «оставить всё» по
`b5/p08-b1` (август `epochs/e-aug`, сентябрь 01–15 `epochs/e-archive`, 16–23 `tmp-t29/rec`) → `_lib.load_rounds` →
`one_per_coin` (= правило «занята» portfolio-sim при потолке 0). Входы не меняются; меняется только выход (оценка
exit-sim: без очереди и латентности, порядок high/low внутри минуты неизвестен — §14 02.10 02:16 п. 3).

  gate     ворота §9 п. 4: `b1-sim` против закрытий движка на сутках `--days` (печать — только таблица метрик b1-sim)
  coverage покрытие свечами по суткам: сделки без файла/без свечей/с дырами в окне/без RV суток; исходов нет
  check    входной путь: закрытия «выход движка как есть» против принятых `ps-closes.json` П-08 (до всякого пересчёта)
  cells   клетки → `<out>/h9-b-<клетка>/ps-closes.json` (схема `p08-run.py`: {"п07b": {период: {потолок: [[мс, $]]}}}) и
           `<tag>/<сутки>/<набор>/rounds.csv` с пересчитанными выходами (для `symbol_map` общего читающего скрипта);
           числа исходов в вывод не печатаются

    python3 p10-exitsim.py gate --days 2026-08-03,2026-09-05,2026-09-06
    python3 p10-exitsim.py cells --cells b1-sim,g24-x25,g24-x4,g25-chand,g109-part
"""
import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/alpha")
SEARCH = (HERE, os.path.join(HOME, "tmp-p10", "code"), os.path.join(HOME, "bin"))

SET_ = "t-bid-btc4h-q1"
FORM_B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
BASE_SUB = "b5/p08-b1"
DROP = {"TRXUSDT"}  # В-105
MONTHS = {
    "август": {"parts": [("aug", "epochs/e-aug")], "klines": ["epochs/e-aug/study/klines"]},
    "сентябрь": {"parts": [("hist", "epochs/e-archive"), ("rec", "tmp-t29/rec")], "klines": ["study/klines"]},
}
PERIOD_OF_TAG = {"aug": "август", "hist": "история", "rec": "запись"}
ACCEPTED = {"0": "tmp-p08/run/h9-b-p08-keepall/ps-closes.json", "3": "tmp-p08/run/h9-b-p08-keepall-b2/ps-closes.json"}  # B1 / B2
DEPOSIT, GAP_PCT = 2500, 59.7  # как в `p07-h9h10.portfolio_sim_for`

G24_WINDOW_MIN = 5  # P-10 §6.2 T: «RV(5 м)»; в E29/E30 считались окна 15/30/60 (exit-sim `n`)
CHAND_QUIET_RV = 0.0262  # Г-25: RV суток ≤ 262 bps (E30)
# допуск ворот — Судья, P-10 §14 02.10 02:16 п. 3
TOL = {"n": 30, "reason": 0.90, "median": 2.0, "p95": 25.0, "usd_rel": 0.10}


def load_mod(name, fname):
    for d in SEARCH:
        p = os.path.join(d, fname)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location(name, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    sys.exit(f"нет {fname} рядом ({', '.join(SEARCH)})")


X = load_mod("exit_sim", "exit-sim.py")
L = X._lib
P = L._portfolio
MIN_MS = X.MIN_MS
NO_BTC = X.Bars([])  # BTC нужен только форме `btc` exit-sim; клеткам T/U/V — нет


def cell_spec(name):
    return {
        "b1-sim": {"kind": "fix"},
        "g24-x25": {"kind": "volexp", "p": 2.5, "n": G24_WINDOW_MIN, "mode": "exit"},
        "g24-x4": {"kind": "volexp", "p": 4, "n": G24_WINDOW_MIN, "mode": "exit"},
        "g25-chand": {"kind": "chand_quiet", "p": CHAND_QUIET_RV},
        "g109-part": {"kind": "part"},
    }[name]


CELLS = ["b1-sim", "g24-x25", "g24-x4", "g25-chand", "g109-part"]


def exit_part(tr, coin, s24):
    """Г-109: полпозиции — лимитный тейк на +STOP (1:1); пока он не взят, вся позиция под правилами B1, остаток — трейл B1."""
    b = X.simulate(tr, coin, NO_BTC, {"kind": "fix"}, s24)
    if b is None:
        return None
    a = X.simulate(tr, coin, NO_BTC, {"kind": "fix", "take": X.STOP}, s24)
    taken = a is not None and a[1] == "take" and a[2] < b[2]  # тот же бар — защитный выход раньше тейка, как порядок в exit-sim
    leg_a = a if taken else b
    return 0.5 * (leg_a[0] + b[0]), b[1], b[2], (a[2] if taken else None)


def evaluate(cell, tr, coin, s24):
    """(net_bps, причина, минута выхода, минута половины или None) или None, если свечей нет."""
    if cell == "engine":  # выходы движка как есть — проверка входного пути против принятых закрытий
        return tr["net"], tr["reason"], tr["t1"] // 1_000_000, None
    if cell == "g109-part":
        return exit_part(tr, coin, s24)
    res = X.simulate(tr, coin, NO_BTC, cell_spec(cell), s24)
    return None if res is None else (*res, None)


def run_busy_replay(src, dst):
    path = next((os.path.join(d, "busy-replay.py") for d in SEARCH if os.path.exists(os.path.join(d, "busy-replay.py"))), None)
    if path is None:
        sys.exit("нет busy-replay.py")
    r = subprocess.run([sys.executable, path, src, dst, "--sets", SET_], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"busy-replay {src}: {r.stdout[-500:]} {r.stderr[-500:]}")


def prepare(month, home, work, days=None):
    """Принятые сделки B1 месяца + свечи монет + RV суток; `days` — только сделки этих суток входа (после one_per_coin)."""
    cfg = MONTHS[month]
    rows = []
    for tag, h in cfg["parts"]:
        dst = os.path.join(work, "br", tag)
        run_busy_replay(os.path.join(home, h, BASE_SUB), dst)
        for r in L.load_rounds(dst, ".", SET_, FORM_B1):
            if r["sym"] not in DROP:
                rows.append({**r, "part": tag})
    taken = L.one_per_coin(rows)
    kdirs = [os.path.join(home, k) for k in cfg["klines"]]
    coins, s24 = {}, {}
    taken = [r for r in taken if days is None or L.day_of(r["t0"]) in days]
    for r in taken:
        if r["sym"] not in coins:
            coins[r["sym"]] = X.Bars([os.path.join(d, f"ref-{r['sym']}-1m.csv") for d in kdirs])
        s24[(r["sym"], r["t0"])] = X.rv_24h(coins[r["sym"]], r["t0"] // 1_000_000 // MIN_MS * MIN_MS - MIN_MS)
    return {"month": month, "taken": taken, "coins": coins, "s24": s24, "klines": kdirs, "work": work}


def with_exit(tr, res):
    # время выхода — начало минуты выхода, как в exit-sim.main (`res[2]`)
    return {**tr, "net": res[0], "reason": res[1], "t1": res[2] * 1_000_000}


def agreement(pairs):
    """pairs: [(строка движка, (net, причина, …))] → метрики ворот."""
    n = len(pairs)
    d = sorted(abs(res[0] - r["net"]) for r, res in pairs)
    usd_e = sum(r["net"] / 1e4 * r["usd"] for r, _ in pairs)
    usd_s = sum(res[0] / 1e4 * r["usd"] for r, res in pairs)
    same = sum(1 for r, res in pairs if res[1] == r["reason"])
    return {"n": n, "reason_share": same / n if n else None,
            "median": d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2 if n else None,
            "p95": d[min(n - 1, math.ceil(0.95 * n) - 1)] if n else None,
            "usd_engine": usd_e, "usd_sim": usd_s,
            "usd_rel": abs(usd_s - usd_e) / abs(usd_e) if usd_e else None}


def gate_table(g):
    def yn(ok):
        return "да" if ok else "нет"
    rel = g["usd_rel"]
    rows = [
        ("сделок (с свечами)", f"{g['n']}", f"≥ {TOL['n']}", yn(g["n"] >= TOL["n"])),
        ("причина выхода совпала", f"{g['reason_share']:.1%}", f"≥ {TOL['reason']:.0%}", yn(g["reason_share"] >= TOL["reason"])),
        ("|Δ net_bps| медиана", f"{g['median']:.2f}", f"≤ {TOL['median']:g}", yn(g["median"] <= TOL["median"])),
        ("|Δ net_bps| p95 (nearest-rank)", f"{g['p95']:.2f}", f"≤ {TOL['p95']:g}", yn(g["p95"] <= TOL["p95"])),
        ("сумма $: пересчёт против движка", f"{g['usd_sim']:+.2f} против {g['usd_engine']:+.2f}" + ("" if rel is None else f" (откл. {rel:.1%})"),
         f"±{TOL['usd_rel']:.0%}", yn(rel is not None and rel <= TOL["usd_rel"])),
    ]
    return rows


def _stop_missed(coin, r):
    """Свеча минуты выхода движка не дошла до уровня стопа: стоп сработал по стакану, а не по сделке."""
    m = r["t1"] // 1_000_000 // MIN_MS * MIN_MS
    bar = coin.d.get(m)
    return bar is not None and bar[2] > r["entry"] * (1 - X.STOP)


def cmd_gate(a):
    by_month = defaultdict(set)
    if a.days == "all":  # диагностика: все сутки месяцев (только b1-sim против движка)
        by_month = {m: None for m in MONTHS}
    for d in ([] if a.days == "all" else [d for d in a.days.split(",") if d]):
        by_month["август" if d.startswith("2026-08") else "сентябрь"].add(d)
    pairs, fallback, per_day, M_of = [], 0, Counter(), {}
    for month, dset in by_month.items():
        M = prepare(month, a.home, a.work, dset)
        for tag, _ in MONTHS[month]["parts"]:
            M_of[tag] = M
        for r in M["taken"]:
            res = evaluate("b1-sim", r, M["coins"][r["sym"]], M["s24"][(r["sym"], r["t0"])])
            if res is None:
                fallback += 1
                continue
            pairs.append((r, res))
            per_day[L.day_of(r["t0"])] += 1
    print("сутки ворот:", ", ".join(f"{d} ({per_day[d]} сд)" for d in sorted(per_day)), f"| без свечей, исключены: {fallback}")
    if not pairs:
        print("в указанных сутках нет сделок B1 — ворота не считаются")
        return 2
    g = agreement(pairs)
    print("\n| метрика | значение | допуск | да/нет |\n|---|---|---|---|")
    for row in gate_table(g):
        print("| " + " | ".join(row) + " |")
    ok = g["n"] >= TOL["n"] and g["reason_share"] >= TOL["reason"] and g["median"] <= TOL["median"] and g["p95"] <= TOL["p95"] \
        and g["usd_rel"] is not None and g["usd_rel"] <= TOL["usd_rel"]
    print("\nИТОГ ворот b1-sim:", "ЗЕЛЁНОЕ" if ok else "ВНЕ ДОПУСКА — не подгонять, разобрать причины")
    mat = Counter((r["reason"], res[1]) for r, res in pairs)
    print("\nпричины: движок → пересчёт (сделок)")
    for (e, s), n in sorted(mat.items()):
        print(f"  {e:>9} → {s:<9} {n}{'' if e == s else '  <- расхождение'}")
    part = sum(1 for r, _ in pairs if r["fill"] < 0.999)
    print(f"\nс частичным заполнением лестницы (fill_frac < 1): {part} из {len(pairs)}; расхождения по ним:",
          sum(1 for r, res in pairs if r["fill"] < 0.999 and res[1] != r["reason"]))
    if a.details:
        print("\nпо причине выхода движка: сделок | |Δ| медиана | |Δ| p95 | смещение (пересчёт − движок), bps | $ движок → пересчёт")
        for e in sorted({r["reason"] for r, _ in pairs}):
            sub = [(r, res) for r, res in pairs if r["reason"] == e]
            ag = agreement(sub)
            bias = sum(res[0] - r["net"] for r, res in sub) / len(sub)
            print(f"  {e:>9}: {len(sub):>3} | {ag['median']:6.2f} | {ag['p95']:6.2f} | {bias:+7.2f} | {ag['usd_engine']:+8.2f} → {ag['usd_sim']:+8.2f}")
        for name, sub in (("заполнена полностью (fill = 1)", [(r, res) for r, res in pairs if r["fill"] >= 0.999]),
                          ("частичное заполнение (fill < 1)", [(r, res) for r, res in pairs if r["fill"] < 0.999])):
            if sub:
                ag = agreement(sub)
                print(f"  {name}: {len(sub):>3} | |Δ| медиана {ag['median']:.2f} | p95 {ag['p95']:.2f}")
        stops = [(r, res) for r, res in pairs if r["reason"] == "stop"]
        print("\nстопы движка, у которых свеча минуты выхода не дошла до уровня стопа (low > стоп): "
              + str(sum(1 for r, _ in stops if _stop_missed(M_of[r["part"]]["coins"][r["sym"]], r))) + f" из {len(stops)}")
        worst = sorted(pairs, key=lambda z: -abs(z[1][0] - z[0]["net"]))[:5]
        print("пять наибольших |Δ|:", "; ".join(f"{r['sym']} {r['reason']}→{res[1]} {res[0] - r['net']:+.1f}" for r, res in worst))
        rest = [(r, res) for r, res in pairs if r is not worst[0][0]]
        print("без наибольшего: ", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in agreement(rest).items()})
    bad = sorted(((abs(res[0] - r["net"]), r, res) for r, res in pairs if res[1] != r["reason"]), key=lambda z: -z[0])[:8]
    for dlt, r, res in bad:
        print(f"  {r['sym']:<12} t0={dt.datetime.fromtimestamp(r['t0'] / 1e9, dt.timezone.utc):%m-%d %H:%M:%S} "
              f"движок {r['reason']} {r['net']:+.1f} / пересчёт {res[1]} {res[0]:+.1f} (fill {r['fill']:.2f}, Δ {dlt:.1f} bps)")
    return 0 if ok else 2


def cmd_coverage(a):
    tot = defaultdict(Counter)
    need = (X.FILL_WAIT_MIN + X.DEADLINE_MIN + 5) + 1  # минут окна [сигнал, сигнал + 4 ч 35 мин] — то, что читает simulate
    for month in MONTHS:
        M = prepare(month, a.home, a.work)
        for r in M["taken"]:
            c = M["coins"][r["sym"]]
            d = L.day_of(r["t0"])
            m_sig = r["t0"] // 1_000_000 // MIN_MS * MIN_MS
            tot[d]["сделок"] += 1
            if not c.k:
                tot[d]["без файла свечей"] += 1
                continue
            miss = need - len(c.span(m_sig, m_sig + (need - 1) * MIN_MS))
            tot[d]["с дырами в окне"] += miss > 0
            tot[d]["дыр > 5 мин"] += miss > 5
            tot[d]["нет свечей"] += miss == need
            tot[d]["минут не хватает"] += miss
            tot[d]["без RV суток"] += M["s24"][(r["sym"], r["t0"])] is None
    keys = ["сделок", "без файла свечей", "нет свечей", "с дырами в окне", "дыр > 5 мин", "минут не хватает", "без RV суток"]
    print("сутки      " + " ".join(f"{k[:15]:>15}" for k in keys))
    for d in sorted(tot):
        print(f"{d} " + " ".join(f"{tot[d][k]:>15}" for k in keys))
    print("ИТОГО      " + " ".join(f"{sum(tot[d][k] for d in tot):>15}" for k in keys))
    return 0


def write_rounds(dst_root, br_dir, results):
    """rounds.csv busy-replay с пересчитанными выходом/net/причиной — `symbol_map` читающего скрипта ищет t1 по exit_ns."""
    n = 0
    for root, _, files in sorted(os.walk(br_dir)):
        if "rounds.csv" not in files or os.path.basename(root) != SET_:
            continue
        with open(os.path.join(root, "rounds.csv"), encoding="utf-8", newline="") as fh:
            lines = fh.read().splitlines(keepends=True)
        comments = [l for l in lines if l.startswith("#")]
        rd = csv.DictReader(l for l in lines if not l.startswith("#"))
        out = []
        for r in rd:
            res = results.get((r["symbol"], int(r["t0_ns"]))) if r["form"] == FORM_B1 else None
            if res is None:
                continue
            entry = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
            fee = (float(r["exit_px"]) / entry - 1) * 1e4 * int(r["dir"]) - float(r["net_bps"])
            r.update(net_bps=f"{res['net']:.6f}", reason=res["reason"], exit_ns=str(res["t1"]),
                     exit_px=f"{entry * (1 + (res['net'] + fee) / 1e4):.10f}")
            out.append(r)
        dst = os.path.join(dst_root, os.path.relpath(root, br_dir))
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, "rounds.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.writelines(comments)
            w = csv.DictWriter(fh, fieldnames=rd.fieldnames, lineterminator="\n")
            w.writeheader()
            w.writerows(out)
        n += len(out)
    return n


def run_cell(cell, data, klines):
    """Клетка по всем сделкам месяцев → (закрытия по схеме `p08-run`, строки по частям, выходы по частям, без свечей)."""
    per_period, results_by_tag, nfb = defaultdict(list), defaultdict(dict), 0
    for month, M in data.items():
        for r in M["taken"]:
            res = evaluate(cell, r, M["coins"][r["sym"]], M["s24"][(r["sym"], r["t0"])])
            if res is None or cell == "engine":  # нет свечей: выход движка (как в exit-sim.main)
                nfb += res is None
                row = dict(r)
            else:
                row = with_exit(r, res)
            per_period[PERIOD_OF_TAG[r["part"]]].append(row)
            if month == "сентябрь":
                per_period["сентябрь"].append(row)
            results_by_tag[r["part"]][(r["sym"], r["t0"])] = {"net": row["net"], "reason": row["reason"], "t1": row["t1"]}
    out = {"п07b": {}}
    for period in ("история", "запись", "август", "сентябрь"):
        out["п07b"][period] = {}
        if not per_period[period]:
            continue
        for cap in (0, 3):  # 0 — B1, 3 — B2 (потолок 3), как `portfolio-sim --max-pos 0,3`
            sim = P.simulate(per_period[period], ([], []), klines, DEPOSIT, cap, 0, 0, set(), GAP_PCT)
            out["п07b"][period][str(cap)] = sim["_closes"]
    return out, per_period, results_by_tag, nfb


def load_all(a):
    days = set(a.days.split(",")) if a.days != "all" else None
    data = {m: prepare(m, a.home, a.work, days) for m in MONTHS}
    return data, P.Klines([d for M in data.values() for d in M["klines"]])


def cmd_check(a):
    """Входной путь: закрытия «движок как есть» из этого скрипта против принятых `ps-closes.json` П-08 (байт в байт по закрытиям)."""
    data, klines = load_all(a)
    out = run_cell("engine", data, klines)[0]["п07b"]
    refs = {cap: json.load(open(os.path.join(a.home, path), encoding="utf-8"))["п07b"] for cap, path in ACCEPTED.items()}
    ok = True
    print("| период | потолок | закрытий (скрипт / принятое) | совпало |\n|---|---|---|---|")
    for period in ("август", "история", "запись", "сентябрь"):
        for cap in ("0", "3"):
            got, want = out[period].get(cap), refs[cap].get(period, {}).get(cap)
            same = got is not None and [list(x) for x in got] == want
            ok &= same
            print(f"| {period} | {cap} | {len(got or [])} / {len(want or [])} | {'да' if same else 'нет'} |")
    print("ИТОГ:", "совпало" if ok else "НЕ совпало")
    return 0 if ok else 2


def cmd_cells(a):
    names = [c for c in a.cells.split(",") if c]
    if "b1-sim" not in names:
        names = ["b1-sim"] + names  # Δ$ клеток — против b1-sim того же exit-sim (Судья, §14 п. 3)
    data, klines = load_all(a)
    for cell in names:
        out, per_period, results_by_tag, nfb = run_cell(cell, data, klines)
        cdir = os.path.join(a.out, f"h9-b-{cell}")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "ps-closes.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        nrounds = sum(write_rounds(os.path.join(cdir, tag), os.path.join(a.work, "br", tag), results_by_tag[tag])
                      for tag in results_by_tag)
        print(f"{cell}: сделок {sum(len(per_period[p]) for p in ('август', 'сентябрь'))}, без свечей (выход движка) {nfb}, "
              f"строк rounds {nrounds} → {cdir}/ps-closes.json")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["gate", "coverage", "check", "cells"])
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--work", default=os.path.join(HOME, "tmp-p10", "work"))
    ap.add_argument("--out", default=os.path.join(HOME, "tmp-p10", "run"))
    ap.add_argument("--days", default="all", help="gate: список суток через запятую; cells: all или список")
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--details", action="store_true", help="gate: разбор расхождений по причине выхода движка")
    a = ap.parse_args()
    sys.exit({"gate": cmd_gate, "coverage": cmd_coverage, "check": cmd_check, "cells": cmd_cells}[a.cmd](a))


if __name__ == "__main__":
    main()
