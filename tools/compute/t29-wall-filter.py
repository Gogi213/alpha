#!/usr/bin/env python3
"""T-29 (владелец 26.09 ~23:20: «возраст отдельно, деньги отдельно и возраст + деньги отдельно; сильнее всего
интересует 100к + 1 час»): фильтр стены ВЫШЕ текущего у главного варианта (В-104: возраст ≥ 45 мин, пол $10k) —
отбором готовых сделок главного, без нового бэктеста. Ниже текущего (15/30 мин) так нельзя — нужен прогон.

Сделка → её подход в кэше D20: тот же символ, `side=bid`, `arm_ms == t0_ns / 10⁶`, `age_ms ≥ 2 700 000` (как
`p02-variant-filter.py link`). Признаки стены — те же, что проверяет движок в наборе (`sets.rs:441-453`):
возраст `age_ms` на взводе; номинал = `price_tick × tick_size × size_at_arm × qty_step` (тик и шаг лота — из
`study/root-<день>/instruments.csv` тех же суток). Оговорка «без пересчёта ёмкости»: движок держит одну позицию
на монету — сделка, отброшенная фильтром, могла занимать монету и не пускать следующий сигнал; настоящий прогон с
`age=`/`usd_min=` может взять сделки, которых здесь нет. Для точки владельца — справочно, до прогона.

Выход: `<дом>/b5/t29-wall/<день>/<имя>/rounds.csv` (главная форма, только прошедшие сделки) на каждый `--cut`;
`f-all` — все сделки (сверка счёта с базой). Печатает счётчики и квантили номинала по месяцам — без денег.

    python3 t29-wall-filter.py --home epochs/e-aug --day-from 2026-08-01 --day-to 2026-08-31 \\
        --cut a60u100k:60:100000 --cut a60:60:0 --cut u100k:45:100000
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import date, timedelta

MAIN_FORM = "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
MIN_AGE_MS = 2700 * 1000  # В-104
EXCLUDE = {"TRXUSDT"}  # В-105


def days(a: str, b: str):
    d, e = date.fromisoformat(a), date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def quantiles(xs, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = sorted(xs)
    return {str(q): round(xs[min(len(xs) - 1, int(q * len(xs)))]) for q in qs} if xs else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--appr-home", default=None, help="дом кэша D20 и study/root-<день> (запись: «.»)")
    ap.add_argument("--rounds-sub", default="b5/titrc-u500r")
    ap.add_argument("--out-sub", default="b5/t29-wall")
    ap.add_argument("--day-from", required=True)
    ap.add_argument("--day-to", required=True)
    ap.add_argument("--cut", action="append", default=[], help="имя:возраст_мин:номинал_usd")
    a = ap.parse_args()
    ah = a.appr_home or a.home
    cuts = [(n, float(m) * 60_000, float(u)) for n, m, u in (c.split(":") for c in a.cut)]
    cnt = defaultdict(int)
    usd_all, age_all = [], []
    for day in days(a.day_from, a.day_to):
        rp = os.path.join(a.home, a.rounds_sub, day, "t-bid-btc4h-q1", "rounds.csv")
        if not os.path.exists(rp):
            continue
        inst = {}
        with open(os.path.join(ah, "study", f"root-{day}", "instruments.csv"), encoding="utf-8") as f:
            for r in csv.DictReader(l for l in f if not l.startswith("#")):
                inst[r["symbol"]] = (float(r["tick_size"]), float(r["qty_step"]))
        with open(rp, encoding="utf-8", newline="") as f:
            lines = [l for l in f if not l.startswith("#")]
        header = next(csv.reader(lines[:1]))
        rows = [r for r in csv.DictReader(lines) if r["form"] == MAIN_FORM and r["symbol"] not in EXCLUDE]
        out = {"f-all": rows, **{n: [] for n, _, _ in cuts}}
        arm_cache = {}
        for r in rows:
            sym = r["symbol"]
            cnt["total"] += 1
            if sym not in arm_cache:
                arm = defaultdict(list)
                p = os.path.join(ah, "study", "approaches", "D20", day, f"approaches-{sym}.csv")
                if os.path.exists(p):
                    with open(p, encoding="utf-8", newline="") as f:
                        for x in csv.DictReader(f):
                            if x["side"] == "bid" and int(x["age_ms"]) >= MIN_AGE_MS:
                                arm[int(x["arm_ms"])].append(x)
                arm_cache[sym] = arm
            c = arm_cache[sym].get(int(r["t0_ns"]) // 1_000_000, [])
            if len(c) != 1:
                cnt["unlinked"] += 1
                continue
            tick, step = inst[sym]
            usd = int(c[0]["price_tick"]) * tick * int(c[0]["size_at_arm"]) * step
            age = int(c[0]["age_ms"])
            usd_all.append(usd)
            age_all.append(age / 60_000)
            for n, amin, umin in cuts:
                if age >= amin and usd >= umin:
                    out[n].append(r)
                    cnt[n] += 1
        for n, srows in out.items():
            d = os.path.join(a.home, a.out_sub, day, n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "rounds.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=header)
                w.writeheader()
                w.writerows(srows)
    print(json.dumps({"home": a.home, "counts": dict(cnt), "usd_q": quantiles(usd_all),
                      "age_min_q": quantiles(age_all)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
