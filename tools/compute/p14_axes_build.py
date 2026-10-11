#!/usr/bin/env python3
"""TK-188 (В-225): входные файлы registry load-axes из P-14 §3 — params, stacks, hyp_axes.
Источник чисел: docs/research/P-14-fractions.md §3.1/§3.2 + docs/findings/hyp-stack-2026-10-11.csv (прежние точки).
Запуск: python tools/compute/p14_axes_build.py -> docs/findings/p14-axes-{params,stacks,hyp_axes}-2026-10-11.csv
Формат: CSV, разделитель ';', UTF-8, LF; списки точек — через '|'; числа — десятичные или дроби 'a/b' (Fraction)."""
import csv
import os
from fractions import Fraction

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "findings")
DATE = "2026-10-11"
PROTO = "P-14@r2"  # версия протокола: ред. 2 (TK-185), до цитаты владельца

Q9 = "5|10|20|33|50|67|80|90|95"
Q5 = "20|33|50|67|80"
LOG8 = "1/8|1/4|1/2|1|2|4|8"
SEV = "1/8|1/4|3/8|1/2|5/8|3/4|7/8"
U7 = "1/6|1/4|1/3|1/2|2/3|5/6|1"

# param;unit;resolution;axis_type (num|log|cat|circ|quantile);description
PARAMS = [
    ("keep_q", "pct", "1", "quantile", "R1: квантиль отбора колонки касаний (направление фиксировано П-11)"),
    ("common_q", "pct", "1", "quantile", "R1: квантиль составного балла (3 гипотезы common_q)"),
    ("col", "-", "", "cat", "R1: колонка признака таблицы касаний; каждая колонка — своя ось keep_q"),
    ("terc_q", "pct", "1", "quantile", "П-класс: терцили -> квантильная ось; признак — в note"),
    ("usd_min_q", "pct", "1", "quantile", "П-класс: usd_min — квантильная ось (было верхняя/нижняя треть)"),
    ("n", "count", "1", "num", "R2: Г-87, верх — min-лот"),
    ("w", "count", "1", "num", "R2: Г-95, верх — min-лот"),
    ("N", "count", "1", "num", "R2: Г-92"),
    ("u", "frac", "1/12", "num", "R2: доля, Г-92/93"),
    ("K", "count", "1", "num", "R2: Г-93"),
    ("x", "mult", "", "log", "R2: множитель Г-106 (лог-2)"),
    ("gamma", "mult", "", "log", "R2: Г-117 (лог-2)"),
    ("T", "frac", "1/16", "num", "R2: Г-117 доля ttl/дедлайна"),
    ("tol_ticks", "tick", "1", "num", "R2: Г-119"),
    ("trigger", "-", "", "cat", "R2: Г-114 halfstop|halflevel"),
    ("f", "frac", "1/8", "num", "R2: Г-114"),
    ("W", "s", "1", "num", "R2: Г-112 окно, с"),
    ("q", "pct", "1", "quantile", "R2: Г-112 квантиль"),
    ("a", "mult", "", "log", "R2: Г-133 (лог-2)"),
    ("offset", "tick", "1", "num", "R2: Г-65, тики от цены"),
    ("entry_form", "-", "", "cat", "Д: Г-85 форма входа реестра"),
    ("btc_window_h", "h", "1", "num", "Д: Г-10 окно BTC, ч"),
    ("btc_thr_q", "pct", "1", "quantile", "Д: Г-10 порог = квантиль хода BTC по самому месяцу (файл порогов до apply)"),
    ("early_exit_s", "s", "0.5", "num", "Д: Г-110"),
    ("exit_share", "pct", "1", "num", "Д: Г-104 доля снятия (gone50->50, gone90->90)"),
    ("be", "-", "", "cat", "Д: Г-104 безубыток да|нет"),
    ("entry_ttl", "s", "1", "num", "Д: Г-129 (1800 = B1)"),
    ("hist_window_h", "h", "1", "num", "П: Г-47 окно истории"),
    ("hour_utc", "h", "1", "circ", "П: Г-14 час суток, соседи h+-1 mod 24"),
    ("weekday", "-", "", "cat", "П: Г-14 день недели (без плато)"),
    ("funding_min", "min", "1", "num", "П: Г-45 минут до фандинга"),
    ("turnover_share_pct", "pct", "0.01", "num", "П: Г-123 доля оборота, %"),
    ("loss_streak", "count", "1", "num", "П: Г-120 убытков подряд"),
    ("block_h", "h", "1", "num", "П: Г-120 блок, ч"),
    ("rv_ratio", "mult", "0.5", "num", "П: Г-24 порог RV5м/RV4ч"),
    ("chandelier_sigma", "sigma4h", "0.5", "num", "П: Г-25 люстра, sigma_4ч"),
    ("rv_day_q", "pct", "1", "quantile", "П: Г-25 порог RV суток (квантиль)"),
    ("share_1to1", "frac", "1/8", "num", "П: Г-109 доля на 1:1"),
]

# stack;version;param;value;note — B1 П-10 §4 полностью + два варианта Летописи
B1 = [("entry", "ladder3x0..0.0409sw2"), ("stop", "pct2"), ("take", "tr1x1"), ("deadline_s", "14400"),
      ("entry_ttl_s", "1800"), ("set", "t-bid-btc4h-q1"), ("age_s", "2700"), ("btc", "btc4h<=-44.55"),
      ("sigma", "sigma240"), ("latency", "В-68"), ("queue", "prob:3"), ("busy", "off"), ("side", "long"),
      ("universe", "pool v171c (В-210), брак verdict.csv (В-181)"), ("notional_usd", "500"),
      ("window", "2026-01-01..2026-09-30 (В-211)")]


def stack(sid, over, note):
    d = dict(B1)
    d.update(over)
    return [(sid, PROTO, k, v, note if k in over else "") for k, v in d.items()]


STACKS = (stack("B1", {}, "")
          + stack("B1-market", {"entry": "market", "set": "p02-h9e899-market"}, "Г-86 (не титруется в П-14)")
          + stack("B1-before", {"entry": "ladder3x2..20w2-before", "stop": "before", "set": "p02-h10-before"},
                  "Г-105 (не титруется в П-14)"))

# hyp_axes: hyp;stack;engine;param;role;old;f1;lo;hi;n_f1;grid;note
#  role: tit | fixed (задан, не титруется) | pending (точки назначает Инженер до F1)
#  grid: оси с одним id — декартово произведение (полная сетка, не OFAT); пусто — одна ось
HY = []


def ax(h, p, old, f1, grid="", role="tit", note="", engine="rust", stack="B1"):
    pts = f1.split("|") if f1 else []
    nums = []
    for t in pts:
        try:
            nums.append(Fraction(t))
        except (ValueError, ZeroDivisionError):
            pass
    ok = pts and len(nums) == len(pts)
    HY.append((h, stack, engine, p, role, old, f1, min(nums) if ok else "", max(nums) if ok else "",
               len(pts) or "", grid, note))


# R1: 9 одноколоночных, 7 многоколоночных (18 колонок), 3 common_q = 30 осей keep_q
R1 = {"Г-35": ["front_add_max_lots"], "Г-39": ["cancel_life_lots"], "Г-42": ["size_share15_bp"],
      "Г-44": ["opp_wall_ratio_bp"], "Г-04": ["wall_add_max_lots"], "Г-63": ["frontrun_delta_10s_lots"],
      "Г-70": ["vpin_bp"], "Г-75": ["tape_all_lots_15s"], "Г-139": ["obi50_bp"],
      "Г-43": ["best_move_1s_cbps", "best_move_10s_cbps"], "Г-68": ["obi1_bp", "obi5_bp", "obi10_bp", "obi50_bp"],
      "Г-59": ["tape_press_lots_15s", "tape_press_lots_30s", "tape_press_lots_60s"],
      "Г-67": ["best_flips_15s", "best_flips_60s"], "Г-69": ["ofi_10s_lots", "ofi_60s_lots"],
      "Г-71": ["sign_ac_15s_bp", "sign_ac_60s_bp"],
      "Г-82": ["tape_burst_press_15s_bp", "tape_burst_press_30s_bp", "tape_burst_press_60s_bp"]}
for h, cols in R1.items():
    c = "|".join(cols)
    ax(h, "col", c, c, role="fixed" if len(cols) == 1 else "tit",
       note="категориальная; ось keep_q — внутри каждой колонки (вложенные квантили: печатать долю общих сделок соседей)")
    ax(h, "keep_q", Q5, Q9, grid="col×keep_q", note="плато вдоль keep_q внутри колонки")
for h in ("Г-64", "Г-73", "Г-83"):
    ax(h, "common_q", Q5, Q9)

# R2 / Д — таблица §3.2
ax("Г-87", "n", "2|3|4|5", "2|3|4|5|6|7|8|10|12", note="верх — min-лот (проверяет Инженер)")
ax("Г-95", "w", "3|4|5", "2|3|4|5|6|7|8|10|12", note="верх — min-лот (проверяет Инженер)")
ax("Г-92", "N", "3|4|5", "2|3|4|5|6|8|10", grid="N×u")
ax("Г-92", "u", "1/3|2/3|1", U7, grid="N×u")
ax("Г-92", "K", "5", "5", role="fixed")
ax("Г-93", "u", "1/3|2/3|1", U7, grid="u×K")
ax("Г-93", "K", "1|2|3", "1|2|3|4|5|6|8", grid="u×K")
ax("Г-106", "x", "0.5|1|2", LOG8)
ax("Г-117", "gamma", "0.5|1|2", LOG8, grid="gamma×T")
ax("Г-117", "T", "1/4|1/2|1", "1/16|1/8|1/4|3/8|1/2|5/8|3/4|1", grid="gamma×T",
   note="верх — ttl/дедлайн (проверяет Инженер)")
ax("Г-119", "tol_ticks", "0|1|2", "0|1|2|3|4|6|8|12|16")
ax("Г-114", "trigger", "halfstop|halflevel", "halfstop|halflevel", grid="trigger×f",
   note="категориальная; плато только вдоль f внутри категории")
ax("Г-114", "f", "1/4|1/2|3/4", SEV, grid="trigger×f")
ax("Г-112", "W", "15|30|60", "5|10|15|30|60|120|300", grid="W×q")
ax("Г-112", "q", "33|50|67|80", Q9, grid="W×q")
ax("Г-133", "a", "0.5|1|2", LOG8)
ax("Г-65", "offset", "-1|1", "-3|-2|-1|0|1|2|3")
ax("Г-85", "entry_form", "+2|+3|single@fr+1", "", role="pending",
   note="все формы входа реестра (до 9); список назначает Инженер до F1; прежние 3 внутри")
ax("Г-10", "btc_window_h", "1|2|3", "1|2|3|4|6|8|12|24", grid="win×thr")
ax("Г-10", "btc_thr_q", "-21.17|-30.56|-38.75", Q9, grid="win×thr",
   note="прежние точки — абсолютные bps; F1 — квантиль по самому месяцу; совпадение хеша проверяет Инженер")
ax("Г-110", "early_exit_s", "1|2|3", "0.5|1|2|3|5|8|13|20|30|60")
ax("Г-104", "exit_share", "50|90", "10|25|50|75|90", grid="share×be")
ax("Г-104", "be", "да", "да|нет", grid="share×be", note="категориальная")
ax("Г-129", "entry_ttl", "60|300", "10|30|60|120|300|600|900|1200|1800", note="1800 = B1")

# В-227 (TK-202): привязки — ось уже у другой гипотезы, своих клеток нет (engine = bind:<гипотеза-хозяин>)
ax("Г-09", "btc_window_h", "4", "", role="fixed", engine="bind:Г-10", note="М4: ядро B1 (BTC 4 ч) = ось Г-10; отдельной нет")
ax("Г-132", "K", "", "", role="fixed", engine="bind:Г-93", note="М8: ось K×u пирамидинга = F1 Г-93 (K ∈ {1…8}); отдельной клетки нет (r2-spec)")

# П-класс (Python на закрытиях B1; хешей нет)
TERC = {"Г-62": "ER 60м", "Г-74": "разгон: max high 5 баров", "Г-76": "удаление от min60/σ240",
        "Г-22": "оборот 24ч (thin)", "Г-54": "оборот 24ч (thin)", "Г-11": "|ход монеты−BTC| 1ч",
        "Г-48": "доля стопов монеты 14 сут", "Г-49": "доля стопов монеты 14 сут", "Г-31": "положение в диапазоне 60м"}
for h, f in TERC.items():
    ax(h, "terc_q", "33|67", Q9, engine="python", note="признак: " + f)
ax("Г-89", "usd_min_q", "верхняя треть", Q9, engine="python", note="прежнее = верхняя треть")
ax("Г-107", "usd_min_q", "нижняя треть", Q9, engine="python", note="прежнее = нижняя треть")
ax("Г-47", "hist_window_h", "24", "1|2|4|6|12|24|48|72|168", engine="python", note="Г-47 бинарна — без терцилей")
ax("Г-14", "hour_utc", "блоки 00|16-17|02-06+21-23", "|".join(str(i) for i in range(24)), grid="hour|weekday",
   engine="python", note="круговая; вместо 4 наборов — час целиком")
ax("Г-14", "weekday", "сб-вс", "пн|вт|ср|чт|пт|сб|вс", grid="hour|weekday", engine="python",
   note="категориальная, без плато")
ax("Г-45", "funding_min", "60", "5|10|15|30|45|60|90|120|180|240|480", engine="python")
ax("Г-123", "turnover_share_pct", "1", "0.1|0.25|0.5|1|2|5|10|20", engine="python")
ax("Г-120", "loss_streak", "5", "2|3|4|5|6|7|8|9|10", grid="streak×block", engine="python")
ax("Г-120", "block_h", "24", "1|2|4|8|12|24|48", grid="streak×block", engine="python")
ax("Г-24", "rv_ratio", "2.5|4", "1.5|2|2.5|3|4|5|6|8", engine="python", note="exit-sim, upper bound (П-10 §9 п.4)")
ax("Г-25", "chandelier_sigma", "2.5", "1.5|2|2.5|3|4|5", grid="chand×rv", engine="python",
   note="exit-sim, upper bound")
ax("Г-25", "rv_day_q", "RV суток <=262 bps", Q9, grid="chand×rv", engine="python",
   note="набор квантилей Q9 принят по умолчанию — в §3.2 не назван, уточнить у Судьи")
ax("Г-109", "share_1to1", "1/2", SEV, engine="python", note="exit-sim, upper bound")


def w(name, head, rows):
    p = os.path.normpath(os.path.join(OUT, f"p14-axes-{name}-{DATE}.csv"))
    with open(p, "w", encoding="utf-8", newline="") as f:
        c = csv.writer(f, delimiter=";", lineterminator="\n")
        c.writerow(head)
        c.writerows(rows)
    print(p, len(rows))


w("params", ["param", "unit", "resolution", "axis_type", "description"], PARAMS)
w("stacks", ["stack", "version", "param", "value", "note"], STACKS)
w("hyp_axes", ["hyp", "stack", "engine", "param", "role", "old_points", "f1_points", "lo", "hi", "n_f1", "grid", "note"],
  [tuple(str(x) for x in r) for r in HY])


def cells(h):
    g = {}
    for r in HY:
        if r[0] == h and r[4] == "tit":
            g.setdefault(r[10] or r[3], []).append(r)
    t = 0
    for rs in g.values():
        n = 1
        for r in rs:
            n *= r[9]
        t += n
    return t


exp = {"Г-87": 9, "Г-95": 9, "Г-92": 49, "Г-93": 49, "Г-106": 7, "Г-117": 56, "Г-119": 9, "Г-114": 14, "Г-112": 63,
       "Г-133": 7, "Г-65": 7, "Г-10": 72, "Г-110": 10, "Г-104": 10, "Г-129": 9}
bad = [(h, cells(h), e) for h, e in exp.items() if cells(h) != e]
print("R2/Д cells", sum(cells(h) for h in exp), "expected 389 (Г-85 pending, до 9, не включён); mismatch:", bad)
ncol = {r[0]: r[9] for r in HY if r[3] == "col"}
r1 = [r for r in HY if r[3] in ("keep_q", "common_q")]
print("R1 осей keep_q/common_q", sum(ncol.get(r[0], 1) for r in r1), "клеток", sum(ncol.get(r[0], 1) * r[9] for r in r1), "(ждём 30 / 270)")
# прежние числовые точки входят в F1
miss = []
for r in HY:
    if r[4] != "tit" or not r[6]:
        continue
    f1 = set(r[6].split("|"))
    try:
        f1n = {Fraction(x) for x in f1}
        old = {Fraction(x) for x in r[5].split("|")}
        if not old <= f1n:
            miss.append((r[0], r[3], sorted(old - f1n)))
    except (ValueError, ZeroDivisionError):
        pass
print("прежние вне F1 (числовые оси):", miss)
