#!/usr/bin/env python3
"""В-108 (владелец 25.09: «свежие данные сразу допрогонять»): каждые новые закрытые сутки нашей записи —
главный вариант В-104 (бэктест, из `<oos-dir>/<сутки>/<set>/rounds.csv`, тот же ряд, что дозаписывает
`oos-frozen.sh`) и кандидаты выхода Г-24/25/26 (ЗАМОРОЖЕНЫ — не подбираются заново, параметры и границы —
из `docs/findings/loss-days-2026-09-25.md`/`docs/plan/HYPOTHESES.md`), пересчитанные по минутным свечам
(`exit-sim.simulate`, та же модель, что E28–E30) — по каждым суткам и итогом по месяцам отдельно.

Г-24 = `exit-sim` family e29, вариант `volexp p=3` («разгон RV30 ≥ 3× медленной»).
Г-25 = `exit-sim` family e30, вариант `chand_quiet p=0.0262` («люстра 2,5σ4ч для спокойных, RV сутки ≤ 262 bps»).
Г-26 = `family-titrate.py --family-by rv24h`, K=3, правило ЗАФИКСИРОВАНО как в подборе «сентябрь → август»
(`docs/findings/family-titrate-2026-09-25.txt`: границы RV 332/460 bps; семейство 1 (RV ≤ 332) — люстра 2,5σ4ч,
семейство 2 (332 < RV ≤ 460) — стоп 3 %, трейл 1,5/0,5, семейство 3 (RV > 460, и сделки без оценки RV) — стоп
2 %, трейл 1/1 = база) — не новое титрование, применение готового правила той же функцией `exit-sim.simulate`.

«База (пересчёт)» — та же сделка, вариант «kind: fix» (стоп 2 %, трейл 1/1) — разница с «база (бэктест)»
показывает оптимизм пересчёта по минутным свечам (~$35–40/мес по прежним замерам, `loss-days-2026-09-25.md`).
Разница кандидата — с базой пересчёта того же прогона (совпадает набор сделок и модель).

    python3 fresh-days.py --oos-dir b5/titrc-u500r --set t-bid-btc4h-q1 \\
        --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 --klines study/klines \\
        --btc-ref study/regime/ref-BTCUSDT-1m.csv --from-day 2026-09-24 --drop TRXUSDT \\
        --csv study/fresh/fresh-days.csv --summary study/fresh/fresh-summary.txt
"""
import argparse
import importlib.util
import os
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


psim = _load("portfolio-sim")
esim = _load("exit-sim")

# Г-26 (E31, подбор «сентябрь → август», family-titrate-2026-09-25.txt) — граница и назначение семейств
# зафиксированы, дальше не подбираются. Порядок семейств и логика `fam()` — как в family-titrate.py:
# сделка без оценки RV (мало свечей) идёт в семейство 1 (индекс 0), как там же.
FAM26_CUTS = (0.0332, 0.0460)  # bps/1e4: 332, 460
FAM26_VARIANTS = [
    {"label": "Г-26 сем.1 (RV<=332bps): люстра 2.5с4ч", "kind": "chandelier", "p": 2.5},
    {"label": "Г-26 сем.2 (332-460bps): стоп 3%, трейл 1,5/0,5", "kind": "fix", "stop": 0.03, "act": 0.015, "gap": 0.005},
    {"label": "Г-26 сем.3 (>460bps): стоп 2%, трейл 1/1 (= база)", "kind": "fix"},
]
CANDIDATES = [
    ("Г-24 (разгон RV30>=3x)", {"kind": "volexp", "p": 3}),
    ("Г-25 (люстра тихих <=262bps)", {"kind": "chand_quiet", "p": 0.0262}),
]
BASE_RECOMPUTE = "база (пересчёт)"
BASE_BACKTEST = "база (бэктест)"


def fam26(s24):
    if s24 is None:
        return 0
    lo, hi = FAM26_CUTS
    return sum(1 for c in (lo, hi) if s24 > c)


def taken_rows(rows):
    """Одна позиция на монету — так торгует бот (тот же порядок, что exit-sim.py/family-titrate.py)."""
    taken, busy = [], {}
    for r in sorted(rows, key=lambda x: x["t0"]):
        if busy.get(r["sym"], 0) > r["t0"]:
            continue
        busy[r["sym"]] = r["t1"]
        taken.append(r)
    return taken


def month_of(day):
    return day[:7]


class Agg:
    def __init__(self):
        self.n = 0
        self.stops = 0
        self.pnl = 0.0
        self.worst = None

    def add(self, pnl, is_stop):
        self.n += 1
        self.stops += 1 if is_stop else 0
        self.pnl += pnl
        self.worst = pnl if self.worst is None else min(self.worst, pnl)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oos-dir", default="b5/titrc-u500r")
    ap.add_argument("--set", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--klines", default="study/klines", help="каталог ref-<SYM>-1m.csv монет пула")
    ap.add_argument("--btc-ref", default="study/regime/ref-BTCUSDT-1m.csv")
    ap.add_argument("--from-day", default="2026-09-24", help="сутки нашей записи от этой даты включительно (UTC)")
    ap.add_argument("--drop", default="TRXUSDT")
    ap.add_argument("--csv", default="study/fresh/fresh-days.csv")
    ap.add_argument("--summary", default="study/fresh/fresh-summary.txt")
    a = ap.parse_args()
    drop = set(x for x in a.drop.split(",") if x)

    rows_all = psim.load_run(".", a.oos_dir, a.set, a.form)
    rows = [r for r in rows_all if r["sym"] not in drop and psim.day_of(r["t0"]) >= a.from_day]
    taken = taken_rows(rows)

    os.makedirs(os.path.dirname(a.csv) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(a.summary) or ".", exist_ok=True)

    if not taken:
        with open(a.csv, "w", encoding="utf-8") as f:
            f.write("period_type,period,variant,n_trades,n_stops,pnl_usd,worst_trade_usd,diff_vs_base_recompute_usd\n")
        with open(a.summary, "w", encoding="utf-8") as f:
            f.write(f"свежие сутки (с {a.from_day}): сделок нет ни одной — обе (набор={a.set}, форма={a.form}) без строк "
                    f"начиная с {a.from_day} в {a.oos_dir}\n")
        print(f"свежие сутки: 0 сделок с {a.from_day} — {a.csv}/{a.summary} пустые")
        return

    btc = esim.Bars([a.btc_ref])
    coins, s24_cache, fam_cache = {}, {}, {}
    for r in taken:
        if r["sym"] not in coins:
            coins[r["sym"]] = esim.Bars([os.path.join(a.klines, f"ref-{r['sym']}-1m.csv")])
        s24 = esim.rv_24h(coins[r["sym"]], r["t0"] // 1_000_000 // esim.MIN_MS * esim.MIN_MS - esim.MIN_MS)
        s24_cache[id(r)] = s24
        fam_cache[id(r)] = fam26(s24)

    # day/month -> variant -> Agg
    by_day = defaultdict(lambda: defaultdict(Agg))
    by_month = defaultdict(lambda: defaultdict(Agg))
    fam_counts = defaultdict(lambda: [0, 0, 0])
    n_no_rv = 0

    for r in taken:
        day = psim.day_of(r["t0"])
        month = month_of(day)
        s24 = s24_cache[id(r)]
        if s24 is None:
            n_no_rv += 1
        coin = coins[r["sym"]]

        pnl_bt = r["net"] / 1e4 * r["usd"]
        by_day[day][BASE_BACKTEST].add(pnl_bt, r["reason"] == "stop")
        by_month[month][BASE_BACKTEST].add(pnl_bt, r["reason"] == "stop")

        variants = [(BASE_RECOMPUTE, {"kind": "fix"})] + CANDIDATES
        for label, v in variants:
            res = esim.simulate(r, coin, btc, dict(v), s24) or (r["net"], r["reason"], r["t1"] // 1_000_000)
            pnl = res[0] / 1e4 * r["usd"]
            by_day[day][label].add(pnl, res[1] == "stop")
            by_month[month][label].add(pnl, res[1] == "stop")

        f_ = fam_cache[id(r)]
        fam_counts[month][f_] += 1
        v26 = FAM26_VARIANTS[f_]
        res26 = esim.simulate(r, coin, btc, dict(v26), s24) or (r["net"], r["reason"], r["t1"] // 1_000_000)
        pnl26 = res26[0] / 1e4 * r["usd"]
        by_day[day]["Г-26 (семейства E31, границы фикс.)"].add(pnl26, res26[1] == "stop")
        by_month[month]["Г-26 (семейства E31, границы фикс.)"].add(pnl26, res26[1] == "stop")

    variant_order = [BASE_BACKTEST, BASE_RECOMPUTE] + [c[0] for c in CANDIDATES] + ["Г-26 (семейства E31, границы фикс.)"]

    def write_rows(w, period_type, buckets):
        for period in sorted(buckets):
            base_pnl = buckets[period].get(BASE_RECOMPUTE)
            base_pnl = base_pnl.pnl if base_pnl else 0.0
            for label in variant_order:
                ag = buckets[period].get(label)
                if not ag:
                    continue
                diff = ag.pnl - base_pnl
                w.write(f"{period_type},{period},{label},{ag.n},{ag.stops},{ag.pnl:.2f},{ag.worst:.2f},{diff:.2f}\n")

    with open(a.csv, "w", encoding="utf-8") as f:
        f.write("period_type,period,variant,n_trades,n_stops,pnl_usd,worst_trade_usd,diff_vs_base_recompute_usd\n")
        write_rows(f, "day", by_day)
        write_rows(f, "month", by_month)

    with open(a.summary, "w", encoding="utf-8") as sf:
        sf.write(f"Свежие сутки нашей записи (с {a.from_day}) — набор {a.set}, форма {a.form}\n")
        sf.write(f"сделок всего {len(taken)}; без оценки RV суток (мало свечей) — {n_no_rv}\n\n")
        for month in sorted(by_month):
            days_in_month = sorted(d for d in by_day if month_of(d) == month)
            sf.write(f"## {month} (свежих суток {len(days_in_month)}: {', '.join(d[8:] for d in days_in_month)})\n")
            for label in variant_order:
                ag = by_month[month].get(label)
                if not ag:
                    continue
                base_pnl = by_month[month][BASE_RECOMPUTE].pnl
                diff_s = "" if label == BASE_RECOMPUTE else f" (Δ к базе пересчёта {ag.pnl - base_pnl:+.0f}$)"
                stops_s = f", стопов {ag.stops}" if label == BASE_BACKTEST else ""
                sf.write(f"  {label:<48} n={ag.n:<4}{stops_s:<14} PnL {ag.pnl:+7.0f}$ худшая {ag.worst:+7.0f}${diff_s}\n")
            fc = fam_counts[month]
            sf.write(f"  (Г-26 по семействам: сем.1={fc[0]} сем.2={fc[1]} сем.3={fc[2]})\n\n")
        sf.write("## по суткам\n")
        for day in sorted(by_day):
            sf.write(f"{day}:\n")
            for label in variant_order:
                ag = by_day[day].get(label)
                if not ag:
                    continue
                base_pnl = by_day[day][BASE_RECOMPUTE].pnl
                diff_s = "" if label == BASE_RECOMPUTE else f" (Δ {ag.pnl - base_pnl:+.0f}$)"
                stops_s = f", стопов {ag.stops}" if label == BASE_BACKTEST else ""
                sf.write(f"  {label:<48} n={ag.n:<4}{stops_s:<14} PnL {ag.pnl:+7.0f}$ худшая {ag.worst:+7.0f}${diff_s}\n")

    print(f"свежие сутки: {len(taken)} сделок с {a.from_day}, месяцев {len(by_month)} → {a.csv}, {a.summary}")


if __name__ == "__main__":
    main()
