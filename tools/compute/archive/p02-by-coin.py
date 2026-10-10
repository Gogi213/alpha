#!/usr/bin/env python3
"""П-02, R2 п.3 (в): проверка по монетам для Г-28, Г-08, Г-46.

Та же метрика, что блок A (`p02-wall.py`, `p02-wave3-analyze.py`): объединённая разность долей
`bounced` между бакетами за месяц, п.п. Разница с исходными сканами — счётчики по (сутки, монета).
Пороги Г-28 не пересчитываются: берутся замороженные из `p02-wall-counts-2026-09-26.json`.

  scan    — Steam Deck, без numpy: D20 `touches-<SYM>.csv` (Г-28, Г-08) и компактные сутки
            `study/p02e/flow/<день>.csv.gz` (Г-46) → JSON счётчиков (сутки × монета × бакет).
  analyze — сверка сумм по монетам с исходными суточными счётчиками (должны совпасть до касания),
            влияние монеты = эффект месяца − эффект без неё; монета с наибольшим влиянием в сторону
            эффекта убирается, знак без неё в обоих месяцах (R2 п.3, правило `b39d9c5`: «монета — так
            же», как сутки в `p02-r2-read.py`). Справочно — интервал блочного бутстрепа без монеты.

Использование:
    python3 p02-by-coin.py scan --thresholds p02-wall-counts-2026-09-26.json --out p02-coin-counts.json [--jobs 2]
    python p02-by-coin.py analyze --in data/p02/p02-coin-counts-2026-09-27.json --out data/p02/p02-coin-read-2026-09-27.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

EXCLUDE = {"TRXUSDT"}  # В-105, как в исходных сканах

# те же отрезки, что DEFAULT_SPANS в p02-wall.py (24.09 и позже — ресурс П-01, не трогаем)
TOUCH_SPANS = [
    ("epochs/e-aug/study/approaches/D20", "2026-08-01", "2026-08-31"),
    ("epochs/e-archive/study/approaches/D20", "2026-09-01", "2026-09-15"),
    ("study/approaches/D20", "2026-09-16", "2026-09-23"),
]
FLOW_DIRS = ["epochs/e-aug/study/p02e/flow", "epochs/e-archive/study/p02e/flow", "study/p02e/flow"]

# гипотеза: (вариант, файл исходных счётчиков, бакет «за», бакет «против»)
HYPS = {
    "Г-28 (H1)": ("g28_main", "p02-wall-counts-2026-09-26.json", "top", "bottom"),
    "Г-08 (H4)": ("g08_main", "p02-wall-counts-2026-09-26.json", "high", "low"),
    "Г-46 (H5)": ("g46_mismatch60", "p02-wave3-counts-2026-09-26.json", "mismatch", "match"),
}


def daterange(a: str, b: str):
    d, e = date.fromisoformat(a), date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def add(counts, day, sym, variant, bucket, bounced):
    c = counts[day][sym][variant].setdefault(bucket, [0, 0])
    c[0] += bounced
    c[1] += 1


def scan_touch_day(job):
    root, day, stl, sth = job
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for path in sorted(glob.glob(os.path.join(root, day, "touches-*.csv"))):
        sym = os.path.basename(path)[len("touches-"):-len(".csv")]
        if sym in EXCLUDE:
            continue
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            try:
                h = next(r)
            except StopIteration:
                continue
            ist, ifr, irz, iend = (h.index(c) for c in ("stack_levels", "frontrun_lots", "round_zeros", "ended_by_death"))
            imax = max(ist, ifr, irz, iend)
            for row in r:
                if len(row) <= imax:
                    continue
                try:
                    s = (float(row[ist]) if row[ist] else 0.0) + (float(row[ifr]) if row[ifr] else 0.0)
                    rz = int(row[irz]) if row[irz] else 0
                except ValueError:
                    continue
                bounced = 0 if row[iend].strip().lower() == "true" else 1
                if s >= sth:
                    add(counts, day, sym, "g28_main", "top", bounced)
                elif s <= stl:
                    add(counts, day, sym, "g28_main", "bottom", bounced)
                if rz >= 2:
                    add(counts, day, sym, "g08_main", "high", bounced)
                elif rz <= 1:
                    add(counts, day, sym, "g08_main", "low", bounced)
    print(f"[scan] touches {day}", file=sys.stderr, flush=True)
    return json.loads(json.dumps(counts))


def scan_flow_day(path):
    day = os.path.basename(path)[: -len(".csv.gz")]
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol")
            if sym in EXCLUDE or "ended_by_death" not in row:
                continue
            val = row.get("mismatch60", "na")
            if val not in ("0", "1"):
                continue
            bounced = 0 if row["ended_by_death"].strip().lower() == "true" else 1
            add(counts, day, sym, "g46_mismatch60", "mismatch" if val == "1" else "match", bounced)
    print(f"[scan] flow {day}", file=sys.stderr, flush=True)
    return json.loads(json.dumps(counts))


def cmd_scan(a) -> int:
    home = os.path.expanduser(a.alpha_home)
    thr = json.load(open(a.thresholds, encoding="utf-8"))["thresholds"]
    stl, sth = thr["sum_terc_lo"], thr["sum_terc_hi"]
    jobs = [(os.path.join(home, root), d, stl, sth) for root, d0, d1 in TOUCH_SPANS for d in daterange(d0, d1)]
    flows = sorted(p for d in FLOW_DIRS for p in glob.glob(os.path.join(home, d, "20*.csv.gz"))
                   if os.path.basename(p)[:10] <= "2026-09-23")
    out = {}

    def merge(part):
        for day, syms in part.items():
            for sym, vs in syms.items():
                out.setdefault(day, {}).setdefault(sym, {}).update(vs)

    if a.jobs > 1:
        from multiprocessing import Pool
        with Pool(a.jobs) as pool:
            for part in pool.imap_unordered(scan_touch_day, jobs):
                merge(part)
            for part in pool.imap_unordered(scan_flow_day, flows):
                merge(part)
    else:
        for j in jobs:
            merge(scan_touch_day(j))
        for p in flows:
            merge(scan_flow_day(p))
    json.dump({"thresholds_from": a.thresholds, "g28_sum_terc": [stl, sth], "exclude": sorted(EXCLUDE),
               "counts": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, sort_keys=True)
    print(f"[scan] записано {a.out}: {len(out)} суток", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------

def _r2():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("r2", os.path.join(here, "p02-r2-read.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def units_for(counts, days, variant, kf, ka, drop=None):
    """Суточные единицы (t_b, t_n, b_b, b_n) как в p02-r2-read.stat_a, без монет из drop."""
    out = []
    for d in days:
        tb = tn = bb = bn = 0
        for sym, vs in counts.get(d, {}).items():
            if drop and sym in drop:
                continue
            v = vs.get(variant, {})
            tb += v.get(kf, [0, 0])[0]; tn += v.get(kf, [0, 0])[1]
            bb += v.get(ka, [0, 0])[0]; bn += v.get(ka, [0, 0])[1]
        out.append((tb, tn, bb, bn))
    return out


def cmd_analyze(a) -> int:
    R = _r2()
    data_dir = os.path.dirname(os.path.abspath(a.inp))
    counts = json.load(open(a.inp, encoding="utf-8"))["counts"]
    res = {}
    for name, (variant, src, kf, ka) in HYPS.items():
        orig = json.load(open(os.path.join(data_dir, src), encoding="utf-8"))["day_counts"]
        res[name] = {}
        for month in ("2026-08", "2026-09"):
            days = sorted(d for d, v in orig.items() if v["month"] == month
                          and variant in v.get("variants", {}) and d <= "2026-09-23")
            # сверка: сумма по монетам = исходный суточный счётчик
            mism = []
            for d in days:
                ov = orig[d]["variants"][variant]
                u = units_for(counts, [d], variant, kf, ka)[0]
                ou = (ov.get(kf, [0, 0])[0], ov.get(kf, [0, 0])[1], ov.get(ka, [0, 0])[0], ov.get(ka, [0, 0])[1])
                if u != ou:
                    mism.append({"day": d, "coin_sum": u, "orig": ou})
            units = units_for(counts, days, variant, kf, ka)
            est = R.stat_a(units)
            syms = sorted({s for d in days for s in counts.get(d, {})})
            infl = []
            for s in syms:
                rest = R.stat_a(units_for(counts, days, variant, kf, ka, drop={s}))
                n_s = sum(counts.get(d, {}).get(s, {}).get(variant, {}).get(k, [0, 0])[1] for d in days for k in (kf, ka))
                infl.append({"symbol": s, "infl_pp": est - rest if rest is not None else 0.0,
                             "est_without": rest, "n_touches": n_s})
            sgn = 1 if est > 0 else -1
            infl.sort(key=lambda x: -sgn * x["infl_pp"])
            top = infl[0]
            u_wo = units_for(counts, days, variant, kf, ka, drop={top["symbol"]})
            b = math.ceil(len(u_wo) ** (1 / 3))
            lo, hi, p = R.block_boot(u_wo, R.stat_a, b, a.reps, 20260926)
            n_all = sum(x["n_touches"] for x in infl)
            res[name][month] = {
                "n_days": len(days), "est": est, "n_symbols": len(syms), "check_mismatch_days": mism,
                "top_coin": top["symbol"], "top_coin_share_touches": top["n_touches"] / n_all if n_all else None,
                "top_coin_infl_pp": top["infl_pp"], "est_without_top_coin": top["est_without"],
                "sign_kept_without_top_coin": top["est_without"] is not None and top["est_without"] * est > 0,
                "without_top_coin_boot_ref": {"b": b, "ci95": [lo, hi], "p_boot_unadj": p},
                "top5_by_influence": infl[:5],
                "coins_against_sign": sum(1 for x in infl if x["infl_pp"] * sgn < 0),
            }
    json.dump(res, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    for name, r in res.items():
        line = [name]
        for m, v in r.items():
            line.append(f"{m}: {v['est']:+.2f} п.п.; без {v['top_coin']} ({v['top_coin_share_touches']*100:.1f} % касаний) "
                        f"{v['est_without_top_coin']:+.2f} [{v['without_top_coin_boot_ref']['ci95'][0]:+.2f}; "
                        f"{v['without_top_coin_boot_ref']['ci95'][1]:+.2f}] знак {'держится' if v['sign_kept_without_top_coin'] else 'НЕТ'}; "
                        f"сверка {'ок' if not v['check_mismatch_days'] else 'РАСХОЖДЕНИЕ ' + str(len(v['check_mismatch_days']))}")
        print(" | ".join(line))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--alpha-home", default="~/alpha")
    s.add_argument("--thresholds", required=True, help="p02-wall-counts-2026-09-26.json (замороженные пороги Г-28)")
    s.add_argument("--out", required=True)
    s.add_argument("--jobs", type=int, default=2)
    an = sub.add_parser("analyze")
    an.add_argument("--in", dest="inp", required=True, help="рядом должны лежать исходные счётчики блока A")
    an.add_argument("--out", required=True)
    an.add_argument("--reps", type=int, default=20000)
    a = p.parse_args(argv)
    return cmd_scan(a) if a.cmd == "scan" else cmd_analyze(a)


if __name__ == "__main__":
    sys.exit(main())
