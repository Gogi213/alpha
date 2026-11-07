#!/usr/bin/env python3
"""ПРЕДРЕГИСТРАЦИЯ типов сделок по признакам ДО входа (записано и закоммичено ДО чтения исходов: net, code, gross, fund, hold, D, dur, out в анализе не использовались).

Признаки - только прошлое на минуте решения t: глубина g_in, биржи ног, медиана |d_idio| / разброс / доля минут g >= 25,2 за предыдущие 1, 3, 7 ПОЛНЫХ суток, дрейф базиса,
ожидаемое начисление фандинга по последним известным ставкам, протухшая цена (доля пустых минут за 60 мин), односторонность импульса (доля большей из двух минутных доходностей ног),
оборот, возраст серии g >= g_entry на входе. D (длина серии вперёд) - НЕ признак (будущее).
Границы «верхний / нижний терциль» - 2/3 и 1/3 квантили распределения признака по сигналам B1 ФОРМИРОВАНИЯ (измерено, outcome-free; файл types_cuts.json); ноль для фандинга - естественный.
Семейство типов: все атомы (одиночные) + все пары атомов (конъюнкции) + три составных типа теории - см. FAMILY. Минимум для статистики типа: >= 30 сделок и >= 10 различных активов (кластеров).
"""
import itertools
import json
import os

import numpy as np

MIN_TRADES = 30
MIN_ASSETS = 10
Q_HI, Q_LO = 2.0 / 3.0, 1.0 / 3.0
BYBIT, BINGX = 4, 2          # индексы в ts_lib.EXCH = [aster, binance, bingx, bitget, bybit, kraken, lighter, okx]
CUT_FEATS = ["M1", "M3", "M7", "V3d", "F3", "drift7", "fund_day", "share_drv", "minturn"]
STALE_Q = 0.8333333333333334   # q_stale класса крипта из classpar (заморожено на формировании)


def derive(c):
    """c: dict имя -> numpy-массив столбцов таблицы сделок; добавляет производные признаки (только прошлое)."""
    d = dict(c)
    d["g_in_"] = c["g_in"]
    d["minturn"] = np.fmin(c["turnA"], c["turnB"])
    dirp = c["dir"] > 0                                  # dir=+1: шорт A (A дороже), лонг B
    exA, exB = c["exA"], c["exB"]
    d["byb_short"] = (dirp & (exA == BYBIT)) | (~dirp & (exB == BYBIT))
    d["byb_long"] = (dirp & (exB == BYBIT)) | (~dirp & (exA == BYBIT))
    d["bingx_any"] = (exA == BINGX) | (exB == BINGX)
    drv_ok = np.isfinite(c["drv"])
    drvA = drv_ok & (c["drv"] == 0)
    drvB = drv_ok & (c["drv"] == 1)
    d["pump"] = (dirp & drvA) | (~dirp & drvB)           # сдвинулась ДОРОГАЯ (шорт) нога
    d["dump"] = drv_ok & ~d["pump"]                      # сдвинулась ДЕШЁВАЯ (лонг) нога
    d["drv_bybit"] = (drvA & (exA == BYBIT)) | (drvB & (exB == BYBIT))
    d["stale_hi"] = np.fmax(c["staleA"], c["staleB"]) > STALE_Q
    return d


def compute_cuts(d_formation):
    """2/3 и 1/3 квантили признаков по сигналам формирования (B1); только столбцы признаков."""
    cuts = {}
    for f in CUT_FEATS:
        x = d_formation[f]
        x = x[np.isfinite(x)]
        cuts[f] = dict(hi=float(np.quantile(x, Q_HI)), lo=float(np.quantile(x, Q_LO)), n=int(len(x)))
    return cuts


def atoms(d, cuts):
    """-> dict имя атома -> булев массив. Атомы с неопределённым признаком (nan) ложны."""
    g = d["g_in"]
    a = {}
    a["DEP1"] = g < 50.0
    a["DEP2"] = (g >= 50.0) & (g < 80.0)
    a["DEP3"] = (g >= 80.0) & (g < 120.0)
    a["DEP4"] = (g >= 120.0) & (g < 200.0)
    a["DEP5"] = g >= 200.0
    a["BY"] = d["byb"] == 1
    a["BYS"] = d["byb_short"]
    a["BYL"] = d["byb_long"]
    a["BX"] = d["bingx_any"]
    with np.errstate(invalid="ignore"):
        for f, nm in (("M1", "M1"), ("M3", "M3"), ("M7", "M7"), ("V3d", "V3"), ("F3", "F3"), ("drift7", "DR7")):
            a[nm + "hi"] = d[f] >= cuts[f]["hi"]
            a[nm + "lo"] = d[f] <= cuts[f]["lo"]
        a["FA"] = d["fund_day"] < 0.0
        a["FAS"] = d["fund_day"] <= cuts["fund_day"]["lo"]
        a["FF"] = d["fund_day"] >= cuts["fund_day"]["hi"]
        a["ST"] = d["stale_hi"]
        a["ONE"] = d["share_drv"] >= cuts["share_drv"]["hi"]
        a["BAL"] = d["share_drv"] <= cuts["share_drv"]["lo"]
        a["PUMP"] = d["pump"]
        a["DUMP"] = d["dump"]
        a["DBY"] = d["drv_bybit"]
        a["LQlo"] = d["minturn"] <= cuts["minturn"]["lo"]
        a["LQhi"] = d["minturn"] >= cuts["minturn"]["hi"]
        a["AGE0"] = d["age"] == 0
        a["AGE1"] = d["age"] >= 1
    return a


def composites(a, d):
    """Три составных типа теории (правила заданы здесь, до исходов).
    NOISE  - «шум»: глубина < 80 bps.
    STRUCT - «структурный разрыв»: пара уже была часто расходящейся в прошлые 3 суток (доля минут g >= 25,2 в верхнем терциле) ИЛИ медиана |d| за 3 суток в верхнем терциле, и глубина >= 120.
    PRESS  - «разовое давление на одной бирже»: спокойная пара (медиана |d| за 7 суток в нижнем терциле), импульс односторонний (верхний терциль), глубина >= 80, цена не протухла."""
    g = d["g_in"]
    c = {}
    c["C_NOISE"] = g < 80.0
    c["C_STRUCT"] = (a["F3hi"] | a["M3hi"]) & (g >= 120.0)
    c["C_PRESS"] = a["M7lo"] & a["ONE"] & (g >= 80.0) & ~a["ST"]
    return c


def exclusive(n1, n2):
    """Пары атомов, которые не могут быть истинны одновременно по определению."""
    groups = [{"DEP1", "DEP2", "DEP3", "DEP4", "DEP5"}, {"BYS", "BYL"}, {"M1hi", "M1lo"}, {"M3hi", "M3lo"}, {"M7hi", "M7lo"}, {"V3hi", "V3lo"}, {"F3hi", "F3lo"}, {"DR7hi", "DR7lo"},
              {"ONE", "BAL"}, {"PUMP", "DUMP"}, {"LQlo", "LQhi"}, {"AGE0", "AGE1"}, {"FA", "FF"}, {"FAS", "FF"}]
    return any(n1 in g and n2 in g for g in groups)


def family(a, comps):
    """Все типы семейства: dict имя -> булев массив. Одиночные атомы, пары (без взаимоисключающих), составные."""
    fam = {}
    for n, m in a.items():
        fam[n] = m
    names = sorted(a)
    for n1, n2 in itertools.combinations(names, 2):
        if exclusive(n1, n2):
            continue
        fam[n1 + "&" + n2] = a[n1] & a[n2]
    fam.update(comps)
    return fam
