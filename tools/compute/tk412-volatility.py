#!/usr/bin/env python3
"""TK-412: волатильность и дневной оборот по свечам 1 м сентября (TK-411).
Использование: tk412-volatility.py <корень свечей> <выход.csv> [процессов]
Корень: каталоги bybit-linear, bybit-spot, gate-futures_usdt, gate-spot с <инструмент>.csv.gz
(t_open_ms,o,h,l,c,v,quote_v,n_trades). Сутки и часы — UTC от 01.09.00:00.
Дневной диапазон = (max h - min l)/o первой свечи суток; σ суток = sqrt(Σ ln(c_i/c_{i-1})²) по последовательным
закрытиям (минуты без сделок — без строки, т. е. нулевая доходность; прыжок через дыру считается одной доходностью).
Тип: плечевой токен (имя вида ABC3L/5S, CSOP…2L), датированный (поставочный, «-ДДМММГГ»), иначе монета.
Час — тот же диапазон по часу. Оборот — Σ quote_v за сутки."""
import csv
import gzip
import math
import re
import os
import statistics
import sys
from multiprocessing import Pool

START = 1788220800000
DAY = 86400000
HOUR = 3600000
USD_Q = ("USDT", "USDC", "USD", "USD1", "USDE", "FDUSD", "DAI", "PYUSD", "PERP", "GUSD", "RLUSD", "USDG", "USDD", "TUSD")
MKT = {"bybit-linear": ("bybit", "linear"), "bybit-spot": ("bybit", "spot"),
       "gate-futures_usdt": ("gate", "futures_usdt"), "gate-spot": ("gate", "spot")}
QUOTES = ("USDT", "USDC", "FDUSD", "PYUSD", "USDE", "USD1", "USD", "DAI", "EUR", "BTC", "ETH", "BRL", "TRY", "AED",
          "PLN", "GBP", "JPY", "BRZ", "RUB", "UAH", "KZT", "NGN", "ARS", "COP", "MXN", "IDR", "SOL", "XRP", "EURI")


LEV = re.compile(r"^[A-Z]{2,}[2-5][LS]$")


def kind_of(ex, sym):
    if "-" in sym:
        return "датированный"
    base = sym.split("_")[0] if ex == "gate" else re.sub(r"(USDT|USDC|PERP)$", "", sym)
    return "плечевой токен" if LEV.match(base) else "монета"


def quote_of(ex, mk, sym):
    s = sym.split("-")[0]
    if ex == "gate":
        return s.split("_")[-1]
    if s.endswith("PERP"):
        return "USDC"
    for q in QUOTES:
        if s.endswith(q):
            return q
    return "?"


def one(args):
    d, name = args
    ex, mk = MKT[d]
    sym = name[:-7]
    path = os.path.join(ROOT, d, name)
    days = {}   # idx -> [open, hi, lo, qv, sumsq, n]
    hours = {}  # idx -> [open, hi, lo]
    prev_c = None
    nmin = 0
    mhi = 0.0
    mlo = float("inf")
    mopen = None
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",")
            try:
                t = int(p[0]); o = float(p[1]); h = float(p[2]); l = float(p[3]); c = float(p[4]); q = float(p[6])
            except (ValueError, IndexError):
                continue
            if o <= 0 or l <= 0 or c <= 0:
                continue
            nmin += 1
            if mopen is None:
                mopen = o
            if h > mhi:
                mhi = h
            if l < mlo:
                mlo = l
            di = (t - START) // DAY
            hi_ = (t - START) // HOUR
            dd = days.get(di)
            if dd is None:
                dd = days[di] = [o, h, l, 0.0, 0.0, 0]
            else:
                if h > dd[1]:
                    dd[1] = h
                if l < dd[2]:
                    dd[2] = l
            dd[3] += q
            dd[5] += 1
            hh = hours.get(hi_)
            if hh is None:
                hours[hi_] = [o, h, l]
            else:
                if h > hh[1]:
                    hh[1] = h
                if l < hh[2]:
                    hh[2] = l
            if prev_c is not None:
                r = math.log(c / prev_c)
                dd[4] += r * r
            prev_c = c
    qt = quote_of(ex, mk, sym)
    usd = "да" if (qt in USD_Q) else "нет"
    base = [ex, mk, sym, kind_of(ex, sym), qt, usd]
    if not days:
        return base + [0, 0] + [""] * 11
    rng = [(v[1] - v[2]) / v[0] * 100 for v in days.values()]
    sig = [math.sqrt(v[4]) * 100 for v in days.values()]
    qv = [v[3] for v in days.values()]
    hr = [(v[1] - v[2]) / v[0] * 100 for v in hours.values()]
    return base + [len(days), nmin,
                   round(statistics.median(rng), 3), round(max(rng), 3),
                   round(statistics.median(sig), 3), round(max(sig), 3),
                   sum(1 for x in hr if x > 5), sum(1 for x in hr if x > 10), round(max(hr), 3),
                   round((mhi - mlo) / mopen * 100, 2),
                   round(statistics.median(qv), 0), round(sum(qv) / len(qv), 0)]


HEAD = ["биржа", "рынок", "инструмент", "тип", "котировка", "котировка_usd", "дней_с_данными", "минут",
        "диапазон_дня_мед_%", "диапазон_дня_макс_%", "sigma_дня_мед_%", "sigma_дня_макс_%",
        "часов_ход_>5%", "часов_ход_>10%", "диапазон_часа_макс_%", "диапазон_месяца_%",
        "оборот_дня_мед_usd", "оборот_дня_средн_usd"]

if __name__ == "__main__":
    ROOT = sys.argv[1]
    out = sys.argv[2]
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    jobs = []
    for d in MKT:
        for name in sorted(os.listdir(os.path.join(ROOT, d))):
            if name.endswith(".csv.gz"):
                jobs.append((d, name))
    with Pool(nproc, initializer=lambda r: globals().__setitem__("ROOT", r), initargs=(ROOT,)) as pool:
        rows = pool.map(one, jobs, chunksize=8)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(rows)
    print("rows", len(rows), "with_data", sum(1 for r in rows if r[6]))
