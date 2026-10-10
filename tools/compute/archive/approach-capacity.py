#!/usr/bin/env python3
"""Ёмкость по подходам (F2 этапа F): где ставить ногу, если постановка — «на подходе» (arm_ms).

Те же таблицы, что у `leg-distance.py` (корзины расстояния от стены, слоты
`t0`/`pre300`/`post300`/`post1800`, «через спред / хоть что-то / целиком»,
условный net), но цель замера — окно взвода: `start_ms` capacity-строки это
`arm_ms`, `end_ms` — `disarm_ms` (замер F2, прогон `bin/approach-capacity.sh`).

Почему не `leg-distance.py`: там markout касания берётся по ключу
`(symbol, start_ms, price_tick)`, а у подхода в этой колонке момент взвода —
ряд `m_*` из `touches-*.csv` привязан к **касанию**. Связка здесь честная:
`approaches-<SYMBOL>.csv` даёт `touch_start_ms` для взвода (пусто — подход
кончился без касания, markout не считается), и markout берётся у касания по
ключу `(symbol, side, price_tick, touch_start_ms)`.

Премия к середине считается **на момент взвода** (`best_t0`/`opp_t0` capacity-
строки — снимок `t0`, а `t0` у подхода это `arm_ms`), то есть net = markout
касания − премия на взводе − 4.41 bps комиссий. Это смещение вниз по времени
(вход стоял до касания) — оговорка печатается в шапке.

    approach-capacity.py <capacity-каталог> <approaches-каталог> <touches-каталог> [usd]
"""
import collections
import csv
import glob
import math
import os
import statistics
import sys

setdir, approaches_dir, touches_dir = sys.argv[1], sys.argv[2], sys.argv[3]
USD = float(sys.argv[4]) if len(sys.argv) > 4 else 1000.0
FEES = 4.41
BINS = [
    (0, 0, "в упор (0 %)"),
    (0.0001, 2, "0–0,02 %"),
    (2.0001, 5, "0,02–0,05 %"),
    (5.0001, 10, "0,05–0,1 %"),
    (10.0001, 15, "0,1–0,15 %"),
    (15.0001, 20, "0,15–0,2 %"),
]
SLOTS = ["t0", "pre300", "post300", "post1800"]


def binof(d):
    for lo, hi, name in BINS:
        if lo <= d <= hi:
            return name
    return None


def day_dirs(root):
    return sorted(p for p in glob.glob(os.path.join(root, "20*")) if os.path.isdir(p))


# 1. Взвод → касание: ключ (symbol, side, price_tick, arm_ms) → touch_start_ms.
touch_of_arm = {}
n_ap = 0
for day in day_dirs(approaches_dir):
    for p in glob.glob(os.path.join(day, "approaches-*.csv")):
        sym = os.path.basename(p)[len("approaches-"):-len(".csv")]
        for r in csv.DictReader(open(p)):
            n_ap += 1
            if r["touch_start_ms"]:
                touch_of_arm[(sym, r["side"], int(r["price_tick"]), int(r["arm_ms"]))] = int(
                    r["touch_start_ms"]
                )

# 2. Markout касаний: ключ (symbol, side, price_tick, start_ms касания).
mk = {}
for day in day_dirs(touches_dir):
    for p in glob.glob(os.path.join(day, "touches-*.csv")):
        sym = os.path.basename(p)[len("touches-"):-len(".csv")]
        for r in csv.DictReader(open(p)):
            mk[(sym, r["side"], int(r["price_tick"]), int(r["start_ms"]))] = {
                h: (float(r[h]) if r[h] not in ("", None) else None)
                for h in ("m_10m", "m_1h", "m_2h")
            }

# 3. Строки ёмкости: набор → слот → корзина → наблюдения.
obs = collections.defaultdict(lambda: collections.defaultdict(list))
n_touch = n_joined = 0
for p in sorted(glob.glob(os.path.join(setdir, "*", "capacity-*.csv"))):
    rows = list(csv.DictReader(open(p)))
    if not rows:
        continue
    tick_px = float(rows[0]["tick_px"])
    lot = float(rows[0]["lot_qty"])
    per = collections.defaultdict(list)
    for r in rows:
        per[(r["symbol"], r["side"], int(r["price_tick"]), int(r["start_ms"]))].append(r)
    for key, rs in per.items():
        n_touch += 1
        ts = touch_of_arm.get(key)
        m = mk.get((key[0], key[1], key[2], ts)) if ts is not None else None
        if m is not None:
            n_joined += 1
        P = key[2]
        for slot in SLOTS:
            qcol = "q_t0" if slot.startswith("post") or slot == "t0" else f"q_{slot}"
            oppcol = "opp_t0" if slot.startswith("post") or slot == "t0" else f"opp_{slot}"
            bestcol = "best_t0" if slot.startswith("post") or slot == "t0" else f"best_{slot}"
            soldcol = {
                "t0": "sold_touch",
                "pre300": None,
                "post300": "sold_post300",
                "post1800": "sold_post1800",
            }[slot]
            perbin = collections.defaultdict(list)
            for r in rs:
                b = binof(float(r["dist_bps"]))
                if b is None:
                    continue
                q = int(r[qcol])
                if q < 0:
                    continue
                tick = int(r["tick"])
                opp = int(r[oppcol])
                best = int(r[bestcol])
                # Через спред — по стороне стены (аудит 21.09, Б2: раньше только бид):
                # у бид-стены нога не ниже лучшего аска, у аск-стены — не выше лучшего бида.
                side = r["side"]
                crossed = opp >= 0 and (
                    (side == "bid" and tick >= opp) or (side == "ask" and tick <= opp)
                )
                sold = (
                    int(r["sold_pre300"]) + int(r["sold_touch"])
                    if slot == "pre300"
                    else int(r[soldcol])
                )
                px = tick * tick_px
                want = math.floor(USD / (px * lot))
                if want <= 0:
                    continue
                got = 0 if crossed else max(0, min(want, sold - q))
                mid = (best + opp) / 2 if (best > 0 and opp > 0) else P
                prem = (tick - mid) / mid * 1e4
                nets = {}
                for h in ("m_10m", "m_1h", "m_2h"):
                    v = (m or {}).get(h)
                    nets[h] = None if v is None else v - prem - FEES
                perbin[b].append((got / want, nets, crossed))
            for b, xs in perbin.items():
                obs[slot][b].append(
                    (
                        statistics.mean(x[0] for x in xs),
                        {
                            h: (
                                statistics.mean([x[1][h] for x in xs if x[0] > 0 and x[1][h] is not None])
                                if any(x[0] > 0 and x[1][h] is not None for x in xs)
                                else None
                            )
                            for h in ("m_10m", "m_1h", "m_2h")
                        },
                        statistics.mean(1.0 if x[2] else 0.0 for x in xs),
                    )
                )

print(
    f"набор {setdir}: подходов {n_ap}, целей {n_touch}, с касанием после (markout есть) "
    f"{n_joined}; нога ${USD:.0f}; правило «наторговано − очередь»; "
    "net = markout касания − премия на момент взвода − 4.41 bps комиссий"
)
print(
    "оговорка: вход стоял с момента взвода, markout меряется от касания; "
    "«через спред» — нога по нашу сторону лучшей чужой цены на взводе (пост-онли отверг бы)"
)
for slot in SLOTS:
    print(f"\n## слот {slot}")
    print(
        "| расстояние от стены | целей | через спред | хоть что-то | целиком | доля ср. |"
        " net 10м (исп.) | net 1ч (исп.) | net 2ч (исп.) |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, _, name in BINS:
        xs = obs[slot].get(name, [])
        if not xs:
            print(f"| {name} | 0 | | | | | | | |")
            continue
        n = len(xs)
        any_ = sum(1 for x in xs if x[0] > 0) / n
        full = sum(1 for x in xs if x[0] >= 0.99) / n
        mf = statistics.mean(x[0] for x in xs)
        cross = statistics.mean(x[2] for x in xs)

        def fmt(h):
            v = [x[1][h] for x in xs if x[0] > 0 and x[1][h] is not None]
            return f"{statistics.mean(v):+.1f} (n={len(v)})" if v else "—"

        print(
            f"| {name} | {n} | {cross:.0%} | {any_:.0%} | {full:.0%} | {mf:.0%} |"
            f" {fmt('m_10m')} | {fmt('m_1h')} | {fmt('m_2h')} |"
        )
