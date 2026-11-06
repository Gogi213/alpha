#!/usr/bin/env python3
"""TK-493 F7: общие функции отбора и параметров клеток (П-37 r2). Только ФОРМИРОВАНИЕ 01.10-14.10.2026 UTC.

Торговое окно (15.10-04.11) здесь нигде не читается: ряды берутся из data/xdl9/asset/*.f32 (закрытия формирования
со стадии F5, 20160 минут от 01.10 00:00 UTC), панели Deck режутся по 15.10 00:00 на стороне Deck (sel_deck_prep.py).
Только numpy и stdlib.
"""
import csv, math, os, re
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "data", "xdl9")
SEL = os.path.join(DATA, "sel")
DOCS = os.path.join(ROOT, "docs", "findings", "xdl9-selection-2026-11-06")

import sys
sys.path.insert(0, HERE)
import asset_lib as AL   # noqa: E402

# сутки от 2020-01-01 UTC (панели .sap): 01.10 = 2465, 15.10 = 2479, 05.11 = 2500 (не включая)
D_FORM0, D_TRADE0, D_END = 2465, 2479, 2500
MIN_DAY = 1440
N_FORM = (D_TRADE0 - D_FORM0) * MIN_DAY            # 20160
EG_FROM = 1 * MIN_DAY                               # окно EG 02.10-14.10: 13 суток (П-37 §7 п.2)
EG_DAYS = 13
EXCH = AL.EXCH
N_RET_MIN = 300                                     # F5, назначено без Судьи

SLUGS = ["crypto", "eq_us", "eq_nonus", "idx_us", "idx_nonus", "metal_energy", "fx"]
SLUG_BIG = {"crypto": "крипта", "eq_us": "акции+ETF", "eq_nonus": "акции+ETF", "idx_us": "индексы",
            "idx_nonus": "индексы", "metal_energy": "металлы+энергия", "fx": "фиат"}


def ensure(d):
    os.makedirs(d, exist_ok=True)
    return d


def session_mask(n=N_FORM):
    """Касса США на формировании: будни 13:30-20:00 UTC (EDT). Минута t открытия свечи входит, если 13:30 <= t < 20:00.
    01.10.2026 - четверг (python weekday 3)."""
    mins = np.arange(n)
    dow = ((mins // MIN_DAY) + 3) % 7
    tod = mins % MIN_DAY
    return (dow < 5) & (tod >= 13 * 60 + 30) & (tod < 20 * 60)


def weekend_mask(n=N_FORM):
    mins = np.arange(n)
    dow = ((mins // MIN_DAY) + 3) % 7
    return dow >= 5


def load_formation():
    """-> rows (контракты со свечами формирования, как asset_lib), C float32 [N, 20160], am {(биржа, символ): строка asset_map}."""
    rows, C = AL.load_contracts()
    am = {}
    with open(os.path.join(DATA, "asset_map.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            am[(r["exchange"], r["symbol"])] = r
    return rows, C, am


def slug_of(a, us):
    """Полоса (каталог) контракта: класс П-37 §4.1 + США/не-США (для акций/ETF и индексов)."""
    big = a["big"]
    if big == "крипта":
        return "crypto"
    if big == "акции+ETF":
        return "eq_us" if us else "eq_nonus"
    if big == "индексы":
        return "idx_us" if us else "idx_nonus"
    if big == "металлы+энергия":
        return "metal_energy"
    if big == "фиат":
        return "fx"
    return None


def sd(x):
    return float(np.std(x)) if len(x) else float("nan")


def kneedle(values):
    """Kneedle как в gate_data.py (П-32 §4): индекс, значение, форма; None при < 3 точек или плоской кривой."""
    ys = sorted(values, reverse=True)
    n = len(ys)
    if n < 3 or ys[0] == ys[-1]:
        return None
    lo, hi = ys[-1], ys[0]
    yn = [(y - lo) / (hi - lo) for y in ys]
    diff = [yn[i] - (1.0 - i / (n - 1)) for i in range(n)]
    concave = sum(diff) >= 0
    key = diff if concave else [-d for d in diff]
    i = max(range(n), key=lambda j: key[j])
    return i, ys[i], "concave" if concave else "convex"


def write_csv(path, header, rows):
    ensure(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def safe(sym):
    return AL.safe_name(sym)


# ------------------------------------------------------------------ MacKinnon (порт eg.rs; для сверки G9)
def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def eg_pvalue(tau):
    if tau != tau or tau > 0.92:
        return 1.0
    if tau < -18.86:
        return 0.0
    if tau <= -2.62:
        z = 2.92 + tau * (1.5012 + tau * 3.9796e-2)
    else:
        z = 2.1945 + tau * (6.4695e-1 + tau * (-2.9198e-1 + tau * -4.2377e-2))
    return norm_cdf(z)
