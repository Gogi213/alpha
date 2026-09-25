#!/usr/bin/env python3
"""П-02, вторая волна: Г-07 (`depth_behind_lots`, терцили) и Г-88 (`repeat_count` на окне 5 мин,
основной вариант заморозки §7/H8) — доля исхода `bounced` по бакетам, тем же способом, что
блок A (`tools/compute/p02-wall.py`, `p02-block-a-2026-09-26.md`): пороги Г-07 считаются один раз
по распределению признака на августе, БЕЗ чтения `ended_by_death`, затем применяются как есть и к
августу, и к сентябрю; Г-88 порог уже был числом в протоколе (`repeat_count > 0` на окне 5 мин —
не калибровка, а гейт).

Читает **компактные** сжатые сутки от `p02-stage2-recompute.sh`
(`symbol,day_utc,side,price_tick,touch_index,ended_by_death,depth_behind_lots,repeat_count`), не
полные `touches-<SYM>.csv` — колонки `stack_levels`/`frontrun_lots`/`round_zeros`/`age_ms`/
`size_at_touch` там нет (это не нужно Г-07/Г-88, они не входят в этот счёт).

Два режима, как у `p02-wall.py`:
  scan    — на Steam Deck, без numpy: читает `.csv.gz`, считает терцили Г-07 (август, только
            значения `depth_behind_lots`), затем доли `bounced` по бакетам обоих месяцев →
            компактный JSON.
  analyze — бутстреп/эффективное N/Холм (нужен numpy) — переносить JSON на машину с numpy или
            использовать чистый python (см. `p02-h10-fix-analyze.py` для образца без numpy).

Использование:
    python3 p02-wall2.py scan --aug-dir epochs/e-aug/study/p02c/aug \
        --sept-dirs epochs/e-archive/study/p02c/sept,study/p02c/sept --out p02-wall2-counts.json
    python3 p02-wall2.py analyze --in p02-wall2-counts.json --out p02-wall2-results.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import glob
import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


def percentile_sorted(sorted_vals: List[float], q: float) -> Optional[float]:
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    k = (n - 1) * q
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def bucket(value: float, lo: float, hi: float) -> Optional[str]:
    if value >= hi:
        return "top"
    if value <= lo:
        return "bottom"
    return None


def iter_compact_rows(path: str):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                depth = float(row["depth_behind_lots"])
                rep = int(row["repeat_count"])
                ended = row["ended_by_death"].strip().lower() == "true"
            except (ValueError, KeyError):
                continue
            yield depth, rep, ended


def list_day_files(dirs: List[str]) -> Dict[str, str]:
    """{день: путь к <день>.csv.gz}, по всем каталогам (сентябрь собран из двух эпох)."""
    out: Dict[str, str] = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "20*.csv.gz"))):
            day = os.path.basename(path)[: -len(".csv.gz")]
            if day in out:
                raise RuntimeError(f"день {day} уже был в {out[day]}, повтор в {path}")
            out[day] = path
    return out


def cmd_scan(args: argparse.Namespace) -> int:
    aug_days = list_day_files([args.aug_dir])
    sept_days = list_day_files(args.sept_dirs.split(","))
    print(f"[scan] август: {len(aug_days)} суток; сентябрь: {len(sept_days)} суток", file=sys.stderr)

    # --- порог Г-07: только август, только depth_behind_lots (ended_by_death не читается) ---
    depth_vals: List[float] = []
    for day, path in sorted(aug_days.items()):
        for depth, _rep, _ended in iter_compact_rows(path):
            depth_vals.append(depth)
    depth_vals.sort()
    thresholds = {
        "depth_terc_lo": percentile_sorted(depth_vals, 1.0 / 3.0),
        "depth_terc_hi": percentile_sorted(depth_vals, 2.0 / 3.0),
        "n_aug_touches_for_threshold": len(depth_vals),
    }
    print(f"[scan] порог Г-07 (август, {len(depth_vals)} касаний): {thresholds}", file=sys.stderr)
    dtl, dth = thresholds["depth_terc_lo"], thresholds["depth_terc_hi"]
    del depth_vals

    # --- счёт по дням: Г-07 (терциль) и Г-88 (repeat_count>0 на окне 5 мин, уже в данных) ---
    day_counts: Dict[str, dict] = {}
    all_days = [("2026-08", aug_days), ("2026-09", sept_days)]
    for month_tag, days in all_days:
        for day, path in sorted(days.items()):
            counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
            n_rows = 0
            for depth, rep, ended in iter_compact_rows(path):
                n_rows += 1
                bounced = 0 if ended else 1
                b = bucket(depth, dtl, dth)
                if b:
                    c = counts["g07_main"][b]
                    c[0] += bounced
                    c[1] += 1
                g88 = "spring" if rep > 0 else "clean"
                c = counts["g88_5min"][g88]
                c[0] += bounced
                c[1] += 1
            day_counts[day] = {
                "month": month_tag,
                "n_rows": n_rows,
                "variants": {k: dict(v) for k, v in counts.items()},
            }
            print(f"[scan] {day} ({month_tag}): {n_rows} строк", file=sys.stderr)

    out = {"thresholds": thresholds, "day_counts": day_counts}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[scan] записано {args.out}: {len(day_counts)} суток", file=sys.stderr)
    return 0


def _load_effective_n():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_scripts = os.path.join(here, "..", "..", ".claude", "skills", "alpha-research", "scripts")
    sys.path.insert(0, os.path.abspath(skill_scripts))
    import effective_n  # type: ignore
    return effective_n


def pooled_share(day_counts, month_prefix, variant, bucket_name, days=None):
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


def cluster_bootstrap(day_counts, days, variant, top_key, bottom_key, n_boot, rng):
    import numpy as np

    rows = []
    for d in days:
        v = day_counts[d]["variants"].get(variant, {})
        top = v.get(top_key, [0, 0])
        bot = v.get(bottom_key, [0, 0])
        rows.append((top[0], top[1], bot[0], bot[1]))
    if not rows:
        return np.array([])
    arr = np.array(rows, dtype=float)
    n = len(rows)
    idx = rng.integers(0, n, size=(n_boot, n))
    resampled = arr[idx]
    sums = resampled.sum(axis=1)
    top_b, top_t, bot_b, bot_t = sums[:, 0], sums[:, 1], sums[:, 2], sums[:, 3]
    with np.errstate(invalid="ignore", divide="ignore"):
        share_top = np.where(top_t > 0, top_b / top_t, np.nan)
        share_bot = np.where(bot_t > 0, bot_b / bot_t, np.nan)
    return share_top - share_bot


def holm(pvalues: List[Tuple[str, float]]):
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


def analyze_variant(day_counts, month_prefix, variant, top_key, bottom_key, n_boot, seed):
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
        p_val = min(1.0, float(2 * min((boot <= 0).mean(), (boot >= 0).mean())))
    else:
        ci_lo = ci_hi = float("nan")
        p_val = float("nan")

    en = _load_effective_n()
    daily_diffs = []
    for d in days:
        v = day_counts[d]["variants"][variant]
        t, b = v[top_key], v[bottom_key]
        if t[1] > 0 and b[1] > 0:
            daily_diffs.append(t[0] / t[1] - b[0] / b[1])
    n_eff = en.effective_n_from_autocorr(daily_diffs) if len(daily_diffs) >= 8 else float(len(daily_diffs))

    return {
        "days": len(days), "n_eff_days": round(float(n_eff), 1),
        "n_touch_top": nt_top, "n_touch_bottom": nt_bot,
        "share_top": share_top, "share_bottom": share_bot,
        "diff_pp": diff * 100.0, "ci95_pp": (ci_lo * 100.0, ci_hi * 100.0), "p_value": p_val,
    }


VARIANTS = {
    "g07_main": ("top", "bottom", "Г-07 основной (терциль depth_behind_lots, .50 грубая версия)"),
    "g88_5min": ("spring", "clean", "Г-88 основной (repeat_count>0 на окне 5 мин vs =0)"),
}


def cmd_analyze(args: argparse.Namespace) -> int:
    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    day_counts = data["day_counts"]
    print("thresholds:", json.dumps(data["thresholds"], ensure_ascii=False))
    print()

    results = {}
    for month_prefix, month_label in (("2026-08", "август"), ("2026-09", "сентябрь")):
        print(f"=== {month_label} ===")
        for variant, (top_key, bottom_key, label) in VARIANTS.items():
            r = analyze_variant(day_counts, month_prefix, variant, top_key, bottom_key,
                                 n_boot=args.n_boot, seed=args.seed)
            results.setdefault(variant, {})[month_prefix] = r
            print(f"{label}: n_days={r['days']} n_eff={r['n_eff_days']} "
                  f"n_touch={r['n_touch_top']}/{r['n_touch_bottom']} "
                  f"share={r['share_top']:.4f}/{r['share_bottom']:.4f} "
                  f"diff={r['diff_pp']:+.2f}pp CI95=[{r['ci95_pp'][0]:+.2f};{r['ci95_pp'][1]:+.2f}]pp "
                  f"p={r['p_value']:.4f}")
        print()

    for month_prefix, month_label in (("2026-08", "август"), ("2026-09", "сентябрь")):
        pvals = [("H3 Г-07", results["g07_main"][month_prefix]["p_value"]),
                 ("H8 Г-88", results["g88_5min"][month_prefix]["p_value"])]
        adj = holm(pvals)
        print(f"Холм ({month_label}, 2 p-значения этого счёта):")
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

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--aug-dir", required=True)
    p_scan.add_argument("--sept-dirs", required=True, help="через запятую — оба каталога сентября")
    p_scan.add_argument("--out", default="p02-wall2-counts.json")
    p_scan.set_defaults(func=cmd_scan)

    p_an = sub.add_parser("analyze")
    p_an.add_argument("--in", dest="inp", required=True)
    p_an.add_argument("--out", default="p02-wall2-results.json")
    p_an.add_argument("--n-boot", type=int, default=5000)
    p_an.add_argument("--seed", type=int, default=20260926)
    p_an.set_defaults(func=cmd_analyze)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
