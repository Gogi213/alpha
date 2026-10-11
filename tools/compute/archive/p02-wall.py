#!/usr/bin/env python3
"""П-02 блок A: Г-28 (сила стены = stack_levels + frontrun_lots) и Г-08 (round_zeros).

Метрика — доля исхода `bounced` (`ended_by_death=false`, `TOUCH_OUTCOME_LABELS`,
`src/lob/touch_axes.rs:194-208`) на подходах в верхнем/нижнем бакете признака, по дням
(`touches-<SYM>.csv`, кэш D20). Два режима:

  scan    — читает сырые `touches-<SYM>.csv` (тяжёлые, не тащить на Windows), без numpy/
            pandas (на Steam Deck 26.09 их нет — проверено). Считает пороги Г-28 ОДИН РАЗ
            по распределению признака на августе (только значения stack_levels/frontrun_lots,
            колонка исхода на этом шаге не читается) и замораживает их для обоих месяцев.
            Печатает компактный JSON (пороги + счётчики bounced/total по дням и бакетам) —
            это всё, что нужно унести с тяжёлой машины.
  analyze — читает JSON(ы) от scan, считает пуловую разницу долей по месяцам, кластерный
            бутстреп по суткам, p-значение, эффективное N (`effective_n.py`, требует numpy —
            запускать на машине, где он есть), поправку Холма. Никаких сырых данных не трогает.

Пул — 76 инструментов без TRX (В-105); TRXUSDT исключается на чтении файла, не на уровне
счёта денег (здесь денег нет, только исход подхода).

Использование:
    # на Steam Deck (без numpy):
    python3 p02-wall.py scan --out counts.json
    # на машине с numpy (после переноса counts.json):
    python3 p02-wall.py analyze --in counts.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

POOL_EXCLUDE = {"TRXUSDT"}

# Роль "threshold" — участвует в расчёте порогов Г-28 (только август); роль "count" — идёт
# в счёт долей bounced по бакетам (оба месяца). Пути — относительно $HOME/alpha (Steam Deck).
DEFAULT_SPANS = [
    # (month_tag, root_dir, day_from, day_to, roles)
    ("2026-08", "epochs/e-aug/study/approaches/D20", "2026-08-01", "2026-08-31", ("threshold", "count")),
    ("2026-09", "epochs/e-archive/study/approaches/D20", "2026-09-01", "2026-09-15", ("count",)),
    ("2026-09", "study/approaches/D20", "2026-09-16", "2026-09-23", ("count",)),
    # 24.09 и позже сознательно не включены — это ресурс П-01 (свежие сутки), П-02 их не трогает.
]
# TK-018 (В-151): июль — только счёт по замороженным порогам августа (описание, не проверка П-02).
JULY_SPAN = ("2026-07", "epochs/e-jul/study/approaches/D20", "2026-07-01", "2026-07-31", ("count",))


def daterange(start: str, end: str) -> List[str]:
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def symbol_from_path(path: str) -> str:
    name = os.path.basename(path)
    # touches-<SYM>.csv
    return name[len("touches-"):-len(".csv")]


def iter_touch_files(root_dir: str, day: str) -> Iterable[str]:
    day_dir = os.path.join(root_dir, day)
    if not os.path.isdir(day_dir):
        return
    for path in sorted(glob.glob(os.path.join(day_dir, "touches-*.csv"))):
        sym = symbol_from_path(path)
        if sym in POOL_EXCLUDE:
            continue
        yield path


def read_rows(path: str):
    """Yield (stack_levels, frontrun_lots, round_zeros, ended_by_death) per touch row.

    Читает по имени колонки (индекс по заголовку файла), не по фиксированной позиции —
    так требует протокол П-02 (раздел «Заморожено перед счётом»).
    """
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        try:
            header = next(r)
        except StopIteration:
            return
        try:
            i_stack = header.index("stack_levels")
            i_fr = header.index("frontrun_lots")
            i_rz = header.index("round_zeros")
            i_end = header.index("ended_by_death")
            i_age = header.index("age_ms")
            i_size = header.index("size_at_touch")
            i_side = header.index("side")
        except ValueError as e:
            raise RuntimeError(f"{path}: колонка не найдена ({e})")
        i_max = max(i_stack, i_fr, i_rz, i_end, i_age, i_size, i_side)
        for row in r:
            if len(row) <= i_max:
                continue
            try:
                stack = float(row[i_stack]) if row[i_stack] != "" else 0.0
                fr = float(row[i_fr]) if row[i_fr] != "" else 0.0
                rz = int(row[i_rz]) if row[i_rz] != "" else 0
                ended = row[i_end].strip().lower() == "true"
                age = float(row[i_age]) if row[i_age] != "" else 0.0
                size = float(row[i_size]) if row[i_size] != "" else 0.0
                side = row[i_side]
            except ValueError:
                continue
            yield stack, fr, rz, ended, age, size, side


def percentile_sorted(sorted_vals: List[float], q: float) -> Optional[float]:
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    k = (n - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def bucket(value: float, lo: float, hi: float) -> Optional[str]:
    if value >= hi:
        return "top"
    if value <= lo:
        return "bottom"
    return None


def cmd_scan(args: argparse.Namespace) -> int:
    home = os.path.expanduser(args.alpha_home)
    spans = []
    for month_tag, rel_root, day_from, day_to, roles in DEFAULT_SPANS + ([JULY_SPAN] if getattr(args, "july", False) else []):
        root_dir = os.path.join(home, rel_root)
        days = daterange(day_from, day_to)
        spans.append((month_tag, root_dir, days, roles))

    # --- Порог Г-28: только август, только значения признака (без исхода) ---
    sum_vals: List[float] = []
    max_vals: List[float] = []
    age_vals: List[float] = []
    size_vals: List[float] = []
    n_files_thr = 0
    for month_tag, root_dir, days, roles in spans:
        if "threshold" not in roles:
            continue
        for day in days:
            for path in iter_touch_files(root_dir, day):
                n_files_thr += 1
                for stack, fr, rz, ended, age, size, side in read_rows(path):
                    s = stack + fr
                    m = stack if stack > fr else fr
                    sum_vals.append(s)
                    max_vals.append(m)
                    age_vals.append(age)
                    size_vals.append(size)
    sum_vals.sort()
    max_vals.sort()
    age_vals.sort()
    size_vals.sort()
    thresholds = {
        "sum_terc_lo": percentile_sorted(sum_vals, 1.0 / 3.0),
        "sum_terc_hi": percentile_sorted(sum_vals, 2.0 / 3.0),
        "sum_quart_lo": percentile_sorted(sum_vals, 0.25),
        "sum_quart_hi": percentile_sorted(sum_vals, 0.75),
        "max_terc_lo": percentile_sorted(max_vals, 1.0 / 3.0),
        "max_terc_hi": percentile_sorted(max_vals, 2.0 / 3.0),
        "age_terc_lo": percentile_sorted(age_vals, 1.0 / 3.0),
        "age_terc_hi": percentile_sorted(age_vals, 2.0 / 3.0),
        "size_terc_lo": percentile_sorted(size_vals, 1.0 / 3.0),
        "size_terc_hi": percentile_sorted(size_vals, 2.0 / 3.0),
        "n_aug_touches_for_threshold": len(sum_vals),
        "n_aug_files_for_threshold": n_files_thr,
    }
    print(f"[scan] пороги Г-28/Г-08-контроль (август, {len(sum_vals)} касаний, {n_files_thr} файлов): "
          f"{thresholds}", file=sys.stderr)
    del sum_vals, max_vals, age_vals, size_vals

    stl, sth = thresholds["sum_terc_lo"], thresholds["sum_terc_hi"]
    sql, sqh = thresholds["sum_quart_lo"], thresholds["sum_quart_hi"]
    mtl, mth = thresholds["max_terc_lo"], thresholds["max_terc_hi"]
    atl, ath = thresholds["age_terc_lo"], thresholds["age_terc_hi"]
    ztl, zth = thresholds["size_terc_lo"], thresholds["size_terc_hi"]

    # --- Счёт по дням: оба месяца, все варианты (Г-28 x3, Г-08 x2) ---
    day_counts: Dict[str, dict] = {}
    for month_tag, root_dir, days, roles in spans:
        if "count" not in roles:
            continue
        for day in days:
            counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # variant -> bucket -> [n_bounced, n_total]
            n_files = 0
            n_touches = 0
            for path in iter_touch_files(root_dir, day):
                n_files += 1
                for stack, fr, rz, ended, age, size, side in read_rows(path):
                    n_touches += 1
                    bounced = 0 if ended else 1
                    s = stack + fr
                    m = stack if stack > fr else fr

                    b = bucket(s, stl, sth)
                    if b:
                        c = counts["g28_main"][b]
                        c[0] += bounced
                        c[1] += 1

                    b = bucket(s, sql, sqh)
                    if b:
                        c = counts["g28_quart"][b]
                        c[0] += bounced
                        c[1] += 1

                    b = bucket(m, mtl, mth)
                    if b:
                        c = counts["g28_max"][b]
                        c[0] += bounced
                        c[1] += 1

                    g08 = "high" if rz >= 2 else ("low" if rz <= 1 else None)
                    if g08:
                        c = counts["g08_main"][g08]
                        c[0] += bounced
                        c[1] += 1

                    b = "high" if rz >= 3 else ("low" if rz <= 1 else None)
                    if b:
                        c = counts["g08_neighbor"][b]
                        c[0] += bounced
                        c[1] += 1

                    # H4 контроль по ковариатам (§3/§9): тот же g08-бакет, внутри страт
                    # возраста/размера/стороны (терцили возраста/размера — по августу, заморожены).
                    if g08:
                        age_bin = "lo" if age <= atl else ("hi" if age >= ath else "mid")
                        c = counts[f"g08_strat_age_{age_bin}"][g08]
                        c[0] += bounced
                        c[1] += 1

                        size_bin = "lo" if size <= ztl else ("hi" if size >= zth else "mid")
                        c = counts[f"g08_strat_size_{size_bin}"][g08]
                        c[0] += bounced
                        c[1] += 1

                        side_bin = "bid" if side == "bid" else "ask"
                        c = counts[f"g08_strat_side_{side_bin}"][g08]
                        c[0] += bounced
                        c[1] += 1

            if day in day_counts and day_counts[day]["month"] != month_tag:
                raise RuntimeError(f"день {day} уже был в другом месяце — пересечение эпох")
            day_counts[day] = {
                "month": month_tag,
                "n_files": n_files,
                "n_touches": n_touches,
                "variants": {k: dict(v) for k, v in counts.items()},
            }
            print(f"[scan] {day} ({month_tag}): {n_files} файлов, {n_touches} касаний", file=sys.stderr)

    out = {
        "thresholds": thresholds,
        "g08_thresholds": {"main": "round_zeros>=2 vs <=1", "neighbor": "round_zeros>=3 vs <=1"},
        "pool_exclude": sorted(POOL_EXCLUDE),
        "day_counts": day_counts,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[scan] записано {args.out}: {len(day_counts)} суток", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# analyze — требует numpy (для effective_n.py); запускать не на Steam Deck
# ---------------------------------------------------------------------------

def _load_effective_n():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_scripts = os.path.join(here, "..", "..", ".claude", "skills", "alpha-research", "scripts")
    sys.path.insert(0, os.path.abspath(skill_scripts))
    import effective_n  # type: ignore
    return effective_n


def pooled_share(day_counts: Dict[str, dict], month_prefix: str, variant: str, bucket_name: str,
                  days: Optional[List[str]] = None) -> Tuple[int, int]:
    nb = nt = 0
    keys = days if days is not None else [d for d, v in day_counts.items() if v["month"].startswith(month_prefix)]
    for d in keys:
        v = day_counts.get(d)
        if v is None:
            continue
        c = v["variants"].get(variant, {}).get(bucket_name)
        if c:
            nb += c[0]
            nt += c[1]
    return nb, nt


def cluster_bootstrap(day_counts: Dict[str, dict], days: List[str], variant: str,
                       top_key: str, bottom_key: str, n_boot: int, rng) -> "object":
    import numpy as np

    rows = []
    for d in days:
        v = day_counts[d]["variants"].get(variant, {})
        top = v.get(top_key, [0, 0])
        bot = v.get(bottom_key, [0, 0])
        rows.append((top[0], top[1], bot[0], bot[1]))
    if not rows:
        return np.array([])
    arr = np.array(rows, dtype=float)  # n_days x 4
    n = len(rows)
    idx = rng.integers(0, n, size=(n_boot, n))
    resampled = arr[idx]  # n_boot x n x 4
    sums = resampled.sum(axis=1)  # n_boot x 4
    top_b, top_t, bot_b, bot_t = sums[:, 0], sums[:, 1], sums[:, 2], sums[:, 3]
    with np.errstate(invalid="ignore", divide="ignore"):
        share_top = np.where(top_t > 0, top_b / top_t, np.nan)
        share_bot = np.where(bot_t > 0, bot_b / bot_t, np.nan)
    return share_top - share_bot


def holm(pvalues: List[Tuple[str, float]]) -> List[Tuple[str, float, float, bool]]:
    """Holm-Bonferroni. Возвращает (имя, p, p_adj, significant@0.05) в исходном порядке."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i][1])
    adj = [0.0] * m
    running_max = 0.0
    for rank, i in enumerate(order):
        p = pvalues[i][1]
        a = min(1.0, (m - rank) * p)
        running_max = max(running_max, a)
        adj[i] = running_max
    return [(pvalues[i][0], pvalues[i][1], adj[i], adj[i] < 0.05) for i in range(m)]


def analyze_variant(day_counts: Dict[str, dict], month_prefix: str, variant: str,
                     top_key: str, bottom_key: str, n_boot: int, seed: int) -> dict:
    import numpy as np

    days = sorted(d for d, v in day_counts.items()
                   if v["month"].startswith(month_prefix)
                   and variant in v["variants"]
                   and top_key in v["variants"][variant]
                   and bottom_key in v["variants"][variant])
    nb_top, nt_top = pooled_share(day_counts, month_prefix, variant, top_key, days)
    nb_bot, nt_bot = pooled_share(day_counts, month_prefix, variant, bottom_key, days)
    share_top = nb_top / nt_top if nt_top else float("nan")
    share_bot = nb_bot / nt_bot if nt_bot else float("nan")
    diff = share_top - share_bot

    rng = np.random.default_rng(seed)
    boot = cluster_bootstrap(day_counts, days, variant, top_key, bottom_key, n_boot, rng)
    boot = boot[~np.isnan(boot)] if boot.size else boot
    if boot.size:
        ci_lo, ci_hi = np.percentile(boot, [2.5, 97.5])
        p_val = 2 * min((boot <= 0).mean(), (boot >= 0).mean())
        p_val = min(1.0, float(p_val))
    else:
        ci_lo = ci_hi = float("nan")
        p_val = float("nan")

    # эффективное N: автокорреляция дневного ряда (top_share - bottom_share)
    en = _load_effective_n()
    daily_diffs = []
    for d in days:
        v = day_counts[d]["variants"][variant]
        t, b = v[top_key], v[bottom_key]
        if t[1] > 0 and b[1] > 0:
            daily_diffs.append(t[0] / t[1] - b[0] / b[1])
    n_eff = en.effective_n_from_autocorr(daily_diffs) if len(daily_diffs) >= 8 else float(len(daily_diffs))

    return {
        "days": len(days),
        "n_eff_days": None if n_eff is None else round(float(n_eff), 1),
        "n_touch_top": nt_top,
        "n_touch_bottom": nt_bot,
        "share_top": share_top,
        "share_bottom": share_bot,
        "diff_pp": diff * 100.0,
        "ci95_pp": (ci_lo * 100.0, ci_hi * 100.0),
        "p_value": p_val,
    }


VARIANTS = {
    "g28_main": ("top", "bottom", "Г-28 основной (терциль суммы stack_levels+frontrun_lots)"),
    "g28_quart": ("top", "bottom", "Г-28 сосед (квартиль суммы)"),
    "g28_max": ("top", "bottom", "Г-28 сосед (терциль max(stack_levels,frontrun_lots))"),
    "g08_main": ("high", "low", "Г-08 основной (round_zeros>=2 vs <=1)"),
    "g08_neighbor": ("high", "low", "Г-08 сосед (round_zeros>=3 vs <=1)"),
}

# H4 контроль по ковариатам (§3/§9) — страты, не отдельные гипотезы Холма.
CONTROL_VARIANTS = {
    "g08_strat_age_lo": ("high", "low", "Г-08 | возраст: нижний терциль"),
    "g08_strat_age_mid": ("high", "low", "Г-08 | возраст: средний терциль"),
    "g08_strat_age_hi": ("high", "low", "Г-08 | возраст: верхний терциль"),
    "g08_strat_size_lo": ("high", "low", "Г-08 | размер: нижний терциль"),
    "g08_strat_size_mid": ("high", "low", "Г-08 | размер: средний терциль"),
    "g08_strat_size_hi": ("high", "low", "Г-08 | размер: верхний терциль"),
    "g08_strat_side_bid": ("high", "low", "Г-08 | сторона: bid"),
    "g08_strat_side_ask": ("high", "low", "Г-08 | сторона: ask"),
}


def cmd_analyze(args: argparse.Namespace) -> int:
    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    day_counts = data["day_counts"]
    print("thresholds:", json.dumps(data["thresholds"], ensure_ascii=False))
    print()

    results = {}
    # TK-018: июль (если есть в счёте scan --july) — описание: в таблицы, не в Холм
    months = (("2026-08", "август"), ("2026-09", "сентябрь")) + (
        (("2026-07", "июль"),) if any(v.get("month") == "2026-07" for v in day_counts.values()) else ())
    for month_prefix, month_label in months:
        print(f"=== {month_label} ===")
        for variant, (top_key, bottom_key, label) in VARIANTS.items():
            r = analyze_variant(day_counts, month_prefix, variant, top_key, bottom_key,
                                 n_boot=args.n_boot, seed=args.seed)
            results.setdefault(variant, {})[month_prefix] = r
            print(f"{label}: n_days={r['days']} n_eff={'не определено' if r['n_eff_days'] is None else r['n_eff_days']} "
                  f"n_touch(top/bot)={r['n_touch_top']}/{r['n_touch_bottom']} "
                  f"share={r['share_top']:.3f}/{r['share_bottom']:.3f} "
                  f"diff={r['diff_pp']:+.2f}pp CI95=[{r['ci95_pp'][0]:+.2f};{r['ci95_pp'][1]:+.2f}]pp "
                  f"p={r['p_value']:.4f}")
        print()

    controls = {}
    for month_prefix, month_label in months:
        print(f"--- {month_label}: Г-08 контроль по возрасту/размеру/стороне (страты, не для Холма) ---")
        for variant, (top_key, bottom_key, label) in CONTROL_VARIANTS.items():
            r = analyze_variant(day_counts, month_prefix, variant, top_key, bottom_key,
                                 n_boot=args.n_boot, seed=args.seed)
            controls.setdefault(variant, {})[month_prefix] = r
            print(f"{label}: n_days={r['days']} n_eff={'не определено' if r['n_eff_days'] is None else r['n_eff_days']} "
                  f"n_touch(hi/lo)={r['n_touch_top']}/{r['n_touch_bottom']} "
                  f"share={r['share_top']:.3f}/{r['share_bottom']:.3f} "
                  f"diff={r['diff_pp']:+.2f}pp CI95=[{r['ci95_pp'][0]:+.2f};{r['ci95_pp'][1]:+.2f}]pp "
                  f"p={r['p_value']:.4f}")
        print()
    results["_controls"] = controls

    # Холм — только по главным вариантам этого счёта (H1=g28_main, H4=g08_main), раздельно по месяцам.
    # Полный блок A (6 гипотез) требует ещё 4 p-значения от других исполнителей — здесь частичная
    # поправка, помечена как таковая (см. отчёт, «Отступления»).
    for month_prefix, month_label in (("2026-08", "август"), ("2026-09", "сентябрь")):
        pvals = [
            ("H1 Г-28", results["g28_main"][month_prefix]["p_value"]),
            ("H4 Г-08", results["g08_main"][month_prefix]["p_value"]),
        ]
        adj = holm(pvals)
        print(f"Холм ({month_label}, частично — 2 из 6 p-значений блока A):")
        for name, p, p_adj, sig in adj:
            print(f"  {name}: p={p:.4f} -> p_adj={p_adj:.4f} значимо@0.05={sig}")
        print()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[analyze] записано {args.out}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Steam Deck: читает touches-*.csv, без numpy")
    p_scan.add_argument("--alpha-home", default="~/alpha", help="корень ~/alpha на Steam Deck")
    p_scan.add_argument("--out", default="p02-wall-counts.json")
    p_scan.add_argument("--july", action="store_true", help="TK-018: добавить июль (счёт, пороги — август)")
    p_scan.set_defaults(func=cmd_scan)

    p_an = sub.add_parser("analyze", help="считает бутстреп/эффективное N/Холм; нужен numpy")
    p_an.add_argument("--in", dest="inp", required=True)
    p_an.add_argument("--out", default="p02-wall-results.json")
    p_an.add_argument("--n-boot", type=int, default=5000)
    p_an.add_argument("--seed", type=int, default=20260926)
    p_an.set_defaults(func=cmd_analyze)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
