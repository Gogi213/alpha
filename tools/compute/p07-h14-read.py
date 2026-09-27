#!/usr/bin/env python3
"""П-07 H14 (и справочно H9р) — чтение клеток по §10 протокола (`docs/research/P-07-g85-titration.md`):
n/$/доля часов с ожиданием перехая > 5 сут по месяцам (из уже посчитанного `h9r-h14-a.json`,
`p07-h9h10.py:kpi_of`), устойчивость (без любых одних суток входа / без любой одной монеты — доля
> 5 сут по `kn.rolling_kpi` над урезанным списком закрытий), парная Δ$ с базой («keep») по суткам
входа блочным бутстрепом (блок ⌈n^{1/3}⌉, n = число календарных суток периода: август 31, сентябрь
23 — те же границы, что у денежной статистики проекта, не усечение KPI H=5).

Данные берёт из уже посчитанных клеток (`tmp-p07/h9-a-h9r-<cell>/<tag>/<day>/t-bid-btc4h-q1/rounds.csv`
— реальные сделки после busy-replay + `.../ps-closes.json` — их $ от `portfolio-sim.py`), новый
busy-replay/portfolio-sim не запускает.

    python3 p07-h14-read.py --cells k1,k2,k3,n2,n3,n4 --base keep
"""
import argparse
import csv
import glob
import importlib.util
import json
import math
import os
import random

HOME = os.path.expanduser("~/alpha")
HERE = os.path.join(HOME, "tmp-p07")
KN_PATH = os.path.join(HOME, "tmp-t32/kpi-newhigh.py")
GROUPS = {"август": ["aug"], "сентябрь": ["hist", "rec"]}
MONTH_DAYS = {"август": 31, "сентябрь": 23}
RNG_SEED = 20260927
N_BOOT = 4000


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_body(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    body = [ln for ln in lines if not ln.startswith("#")]
    return list(csv.DictReader(body)) if body else []


def load_trades(p, cell):
    """{"август": [...], "сентябрь": [...]}, каждая запись {day, symbol, t1_ms, pnl}. `day` — сутки
    ВХОДА (day_utc сигнала), не выхода. pnl сверяется с ps-closes.json по t1_ms (мультимножество —
    на случай совпавших миллисекунд у разных сделок)."""
    out_dir = os.path.join(HERE, f"h9-a-h9r-{cell}")
    closes = json.load(open(os.path.join(out_dir, "ps-closes.json"), encoding="utf-8"))["п07a"]
    trades = {}
    for period, tags in GROUPS.items():
        rows = []
        for tag in tags:
            for d in sorted(glob.glob(os.path.join(out_dir, tag, "20*"))):
                day_utc = os.path.basename(d)
                for row in load_body(os.path.join(d, "t-bid-btc4h-q1", "rounds.csv")):
                    rows.append((day_utc, row["symbol"], int(row["exit_ns"]) // 1_000_000))
        money = [(int(c[0]), float(c[1])) for c in closes.get(period, {}).get("0", [])]
        by_t1 = {}
        for t1, pnl in money:
            by_t1.setdefault(t1, []).append(pnl)
        recs = []
        unmatched = 0
        for day_utc, symbol, t1 in sorted(rows, key=lambda r: r[2]):
            bucket = by_t1.get(t1)
            if not bucket:
                unmatched += 1
                continue
            pnl = bucket.pop(0)
            recs.append({"day": day_utc, "symbol": symbol, "t1_ms": t1, "pnl": pnl})
        trades[period] = recs
        if unmatched:
            print(f"  [{cell}/{period}] ВНИМАНИЕ: {unmatched} сделок из rounds.csv не нашли пару в ps-closes.json")
    return trades


def frac_gt_5d(kn, all_closes):
    r = kn.rolling_kpi(sorted(all_closes), h_days=5)
    return {"aug": r["aug"]["frac_gt_h"], "sep": r["sep"]["frac_gt_h"]}


def robustness(kn, trades):
    """Без каждых одних суток входа / без каждой одной монеты — доля > 5 сут остаётся в каком
    диапазоне (мин/макс по месяцу), по обеим клеткам разом (rolling_kpi всегда над августом+
    сентябрём вместе, как в kpi_of)."""
    all_recs = trades["август"] + trades["сентябрь"]
    all_closes = [(r["t1_ms"], r["pnl"]) for r in all_recs]
    base = frac_gt_5d(kn, all_closes)
    days = sorted({r["day"] for r in all_recs})
    syms = sorted({r["symbol"] for r in all_recs})

    def scan(key):
        out = {"aug": [], "sep": []}
        for v in (days if key == "day" else syms):
            closes = [(r["t1_ms"], r["pnl"]) for r in all_recs if r[key] != v]
            if not closes:
                continue
            f = frac_gt_5d(kn, closes)
            out["aug"].append(f["aug"])
            out["sep"].append(f["sep"])
        return out

    by_day = scan("day")
    by_sym = scan("symbol")
    return {
        "base": base,
        "without_any_day": {"aug_range": [min(by_day["aug"]), max(by_day["aug"])] if by_day["aug"] else None,
                             "sep_range": [min(by_day["sep"]), max(by_day["sep"])] if by_day["sep"] else None},
        "without_any_coin": {"aug_range": [min(by_sym["aug"]), max(by_sym["aug"])] if by_sym["aug"] else None,
                              "sep_range": [min(by_sym["sep"]), max(by_sym["sep"])] if by_sym["sep"] else None},
    }


def daily_series(recs, n_days_label):
    """day_utc(str YYYY-MM-DD) -> $ сумма, только реальные дни с данными (пропуски = 0 при бутстрепе)."""
    out = {}
    for r in recs:
        out[r["day"]] = out.get(r["day"], 0.0) + r["pnl"]
    return out


def block_bootstrap_delta(cell_days, base_days, n_calendar_days, rng):
    """Δ$ по суткам входа (cell - base), календарные сутки периода 1..n (пропуски = 0), блочный
    бутстреп циклическими блоками длиной ceil(n^(1/3)), N_BOOT повторов. Возвращает (point, lo, hi)
    — точка (полная сумма без бутстрепа) и 2.5/97.5 перцентиль повторов."""
    all_days = sorted(set(cell_days) | set(base_days))
    if not all_days:
        return 0.0, 0.0, 0.0
    delta_by_day = [cell_days.get(d, 0.0) - base_days.get(d, 0.0) for d in all_days]
    n = len(delta_by_day)
    point = sum(delta_by_day)
    block = max(1, math.ceil(n_calendar_days ** (1 / 3)))
    if n < 2:
        return round(point, 2), round(point, 2), round(point, 2)
    totals = []
    for _ in range(N_BOOT):
        picked = []
        while len(picked) < n:
            start = rng.randrange(n)
            for k in range(block):
                picked.append(delta_by_day[(start + k) % n])
        totals.append(sum(picked[:n]))
    totals.sort()
    lo = totals[int(0.025 * len(totals))]
    hi = totals[int(0.975 * len(totals)) - 1]
    return round(point, 2), round(lo, 2), round(hi, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", required=True)
    ap.add_argument("--base", default="keep")
    a = ap.parse_args()
    cells = [c.strip() for c in a.cells.split(",") if c.strip()]

    spec = importlib.util.spec_from_file_location("kn", KN_PATH)
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)

    p = load_module(os.path.join(HERE, "p07-h9h10.py"), "p07base")
    kpi_all = json.load(open(os.path.join(HERE, "h9r-h14-a.json"), encoding="utf-8"))

    base_trades = load_trades(p, a.base)
    base_daily = {m: daily_series(base_trades[m], MONTH_DAYS[m]) for m in GROUPS}

    rng = random.Random(RNG_SEED)
    out = {}
    for cell in cells:
        print(f"=== {cell} ===", flush=True)
        trades = load_trades(p, cell)
        rob = robustness(kn, trades)
        deltas = {}
        for m in GROUPS:
            cell_daily = daily_series(trades[m], MONTH_DAYS[m])
            point, lo, hi = block_bootstrap_delta(cell_daily, base_daily[m], MONTH_DAYS[m], rng)
            deltas[m] = {"point": point, "lo": lo, "hi": hi}
        out[cell] = {"kpi": kpi_all.get(cell), "robustness": rob, "delta_vs_base": deltas}
        print(json.dumps(out[cell], ensure_ascii=False, indent=1), flush=True)

    with open(os.path.join(HERE, "h14-read.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("DONE")


if __name__ == "__main__":
    main()
