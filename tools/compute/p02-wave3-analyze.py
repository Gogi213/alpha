#!/usr/bin/env python3
"""П-02, третья очередь: Г-33 (декоративная цепочка), Г-36 (айсберг), Г-46
(расхождение CVD/цены) — доля исхода `bounced` по бакетам, тем же способом,
что блок A/вторая волна (`p02-wall.py`/`p02-wall2.py`): кластерный бутстреп
по суткам, эффективное N, Холм внутри семьи третьей очереди.

Читает компактные сжатые сутки от `p02-wave3-wall-recompute.sh`
(g33_chain2, g33_chain3, g36_iceberg) и `p02-wave3-flow-recompute.sh`
(mismatch60, mismatch300) — раздельно, объединяются по (symbol,day_utc,side,
price_tick,touch_index) только на уровне подсчёта долей (не физическим join
файлов — они пишутся параллельно на разные пути, а тестируемые бакеты внутри
каждого варианта самодостаточны).

Режимы:
  scan    — на Steam Deck, без numpy: подсчёт долей `bounced` по бакетам
            каждого варианта, по суткам -> компактный JSON.
  analyze — бутстреп/эфф.N/Холм (нужен numpy) — переносить JSON на машину с
            numpy, как в `p02-wall2.py`/`p02-h10-fix-analyze.py`.

Использование:
    python3 p02-wave3-analyze.py scan --wall-dirs <аug>,<archive-sept>,<record-sept> \
        --flow-dirs <аug>,<archive-sept>,<record-sept> --out p02-wave3-counts.json
    python3 p02-wave3-analyze.py analyze --in p02-wave3-counts.json --out p02-wave3-results.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple


def list_day_files(dirs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for d in dirs:
        if not d:
            continue
        for path in sorted(glob.glob(os.path.join(d, "20*.csv.gz"))):
            day = os.path.basename(path)[: -len(".csv.gz")]
            if day in out:
                raise RuntimeError(f"день {day} уже был в {out[day]}, повтор в {path}")
            out[day] = path
    return out


def month_of(day: str) -> str:
    return day[:7].replace("-", "")[:6] and day[:7]


def iter_gz_rows(path: str):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            yield row


def cmd_scan(args: argparse.Namespace) -> int:
    wall_days = list_day_files(args.wall_dirs.split(","))
    flow_days = list_day_files(args.flow_dirs.split(","))
    print(f"[scan] wall: {len(wall_days)} суток; flow: {len(flow_days)} суток", file=sys.stderr)

    day_counts: Dict[str, dict] = {}

    for day, path in sorted(wall_days.items()):
        month = day[:7]
        counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        n_rows = 0
        for row in iter_gz_rows(path):
            try:
                ended = row["ended_by_death"].strip().lower() == "true"
            except KeyError:
                continue
            bounced = 0 if ended else 1
            n_rows += 1
            for variant, col in (("g33_chain2", "g33_chain2"), ("g33_chain3", "g33_chain3"), ("g36_iceberg", "g36_iceberg")):
                try:
                    flag = int(row[col])
                except (KeyError, ValueError):
                    continue
                bucket = "yes" if flag == 1 else "no"
                c = counts[variant][bucket]
                c[0] += bounced
                c[1] += 1
        day_counts.setdefault(day, {"month": month, "variants": {}})
        day_counts[day]["variants"].update({k: dict(v) for k, v in counts.items()})
        day_counts[day]["n_wall_rows"] = n_rows
        print(f"[scan] wall {day} ({month}): {n_rows} строк", file=sys.stderr)

    for day, path in sorted(flow_days.items()):
        month = day[:7]
        counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        n_rows = 0
        for row in iter_gz_rows(path):
            try:
                ended = row["ended_by_death"].strip().lower() == "true"
            except KeyError:
                continue
            bounced = 0 if ended else 1
            n_rows += 1
            for variant, col in (("g46_mismatch60", "mismatch60"), ("g46_mismatch300", "mismatch300")):
                val = row.get(col, "na")
                if val not in ("0", "1"):
                    continue  # "na" — нейтральные случаи не входят в бакеты match/mismatch
                bucket = "mismatch" if val == "1" else "match"
                c = counts[variant][bucket]
                c[0] += bounced
                c[1] += 1
        day_counts.setdefault(day, {"month": month, "variants": {}})
        day_counts[day]["variants"].update({k: dict(v) for k, v in counts.items()})
        day_counts[day]["n_flow_rows"] = n_rows
        print(f"[scan] flow {day} ({month}): {n_rows} строк", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"day_counts": day_counts}, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[scan] записано {args.out}: {len(day_counts)} суток", file=sys.stderr)
    return 0


def _load_effective_n():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_scripts = os.path.join(here, "..", "..", ".claude", "skills", "alpha-research", "scripts")
    sys.path.insert(0, os.path.abspath(skill_scripts))
    import effective_n  # type: ignore
    return effective_n


def pooled_share(day_counts, days, variant, bucket_name):
    nb = nt = 0
    for d in days:
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

    days = sorted(
        d
        for d, v in day_counts.items()
        if v["month"].startswith(month_prefix)
        and variant in v["variants"]
        and top_key in v["variants"][variant]
        and bottom_key in v["variants"][variant]
    )
    nb_top, nt_top = pooled_share(day_counts, days, variant, top_key)
    nb_bot, nt_bot = pooled_share(day_counts, days, variant, bottom_key)
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
        "days": len(days),
        "n_eff_days": round(float(n_eff), 1),
        "n_touch_top": nt_top,
        "n_touch_bottom": nt_bot,
        "share_top": share_top,
        "share_bottom": share_bot,
        "diff_pp": diff * 100.0,
        "ci95_pp": (ci_lo * 100.0, ci_hi * 100.0),
        "p_value": p_val,
    }


VARIANTS = {
    "g33_chain2": ("yes", "no", "Г-33 основной (цепочка декоративных >=2)"),
    "g33_chain3": ("yes", "no", "Г-33 сосед (цепочка >=3)"),
    "g36_iceberg": ("yes", "no", "Г-36 основной (айсберг-флаг)"),
    "g46_mismatch60": ("mismatch", "match", "Г-46 основной (несовпадение знака CVD/цены, окно 60с)"),
    "g46_mismatch300": ("mismatch", "match", "Г-46 сосед (окно 300с)"),
}


def cmd_analyze(args: argparse.Namespace) -> int:
    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    day_counts = data["day_counts"]

    results = {}
    for month_prefix, month_label in (("2026-08", "август"), ("2026-09", "сентябрь")):
        print(f"=== {month_label} ===")
        for variant, (top_key, bottom_key, label) in VARIANTS.items():
            r = analyze_variant(day_counts, month_prefix, variant, top_key, bottom_key, n_boot=args.n_boot, seed=args.seed)
            results.setdefault(variant, {})[month_prefix] = r
            print(
                f"{label}: n_days={r['days']} n_eff={r['n_eff_days']} "
                f"n_touch={r['n_touch_top']}/{r['n_touch_bottom']} "
                f"share={r['share_top']:.4f}/{r['share_bottom']:.4f} "
                f"diff={r['diff_pp']:+.2f}pp CI95=[{r['ci95_pp'][0]:+.2f};{r['ci95_pp'][1]:+.2f}]pp "
                f"p={r['p_value']:.4f}"
            )
        print()

    for month_prefix, month_label in (("2026-08", "август"), ("2026-09", "сентябрь")):
        pvals = [
            ("H2 Г-33 (>=2)", results["g33_chain2"][month_prefix]["p_value"]),
            ("H6 Г-36", results["g36_iceberg"][month_prefix]["p_value"]),
            ("H5 Г-46 (60с)", results["g46_mismatch60"][month_prefix]["p_value"]),
        ]
        adj = holm(pvals)
        print(f"Холм ({month_label}, 3 основных p-значения третьей очереди):")
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
    p_scan.add_argument("--wall-dirs", required=True, help="через запятую")
    p_scan.add_argument("--flow-dirs", required=True, help="через запятую")
    p_scan.add_argument("--out", default="p02-wave3-counts.json")
    p_scan.set_defaults(func=cmd_scan)

    p_an = sub.add_parser("analyze")
    p_an.add_argument("--in", dest="inp", required=True)
    p_an.add_argument("--out", default="p02-wave3-results.json")
    p_an.add_argument("--n-boot", type=int, default=5000)
    p_an.add_argument("--seed", type=int, default=20260926)
    p_an.set_defaults(func=cmd_analyze)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
