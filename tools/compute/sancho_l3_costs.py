#!/usr/bin/env python3
"""TK-455 / P-TK455 §3: издержки слоя 3. Тариф и фандинг (измеряемое), проскальзывание не оценивается.
Тариф: Binance USDⓈ-M, Regular User, без VIP/BNB: мейкер 2 bps, тейкер 5 bps. Источник — официальная страница поддержки
https://www.binance.com/en/support/articles/360033544231 (пример «Regular User 0,02 % / 0,05 %»), доступ 30.10.2026.
ДАТИРОВАННОЙ истории изменений базовой ставки найти не удалось (поиск 30.10) -> по §3 года без датированного тарифа идут
как «издержки не оценены, только gross». TARIFF_FROM_MS — первая дата, с которой ставка подтверждена; пока None = нигде не
подтверждена датой; ставка в TARIFF — рабочая, помечается «недатированная» в отчёте (решение о допуске — Судье).
Фандинг: data.binance.vision futures/um/monthly/fundingRate/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip + .CHECKSUM (sha256).
python tools/compute/sancho_l3_costs.py <SYM> <YYYY-MM> <cache_dir>  -> проверка загрузки"""
import hashlib
import io
import os
import sys
import urllib.request
import zipfile
import numpy as np
import pandas as pd

MAKER_BPS, TAKER_BPS = 2.0, 5.0
TARIFF_FROM_MS = None
BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"


def leg_bps(kind):
    return MAKER_BPS if kind == "maker" else TAKER_BPS


def fees_bps(legs):
    """legs: список 'maker'/'taker' по ногам (вход, выход) -> (смесь, все мейкер, все тейкер) в bps."""
    return sum(leg_bps(k) for k in legs), MAKER_BPS * len(legs), TAKER_BPS * len(legs)


def funding_month(sym, ym, cache):
    fn = f"{sym}-fundingRate-{ym}"
    z = os.path.join(cache, fn + ".zip")
    if not os.path.exists(z):
        data = urllib.request.urlopen(f"{BASE}/{sym}/{fn}.zip", timeout=60).read()
        want = urllib.request.urlopen(f"{BASE}/{sym}/{fn}.zip.CHECKSUM", timeout=60).read().decode().split()[0]
        if hashlib.sha256(data).hexdigest() != want:
            raise RuntimeError(f"CHECKSUM {fn}")
        os.makedirs(cache, exist_ok=True)
        open(z, "wb").write(data)
    with zipfile.ZipFile(z) as zf:
        d = pd.read_csv(io.BytesIO(zf.read(zf.namelist()[0])))
    return d[["calc_time", "last_funding_rate"]].rename(columns={"calc_time": "t", "last_funding_rate": "r"})


def funding_bps(times, rates, t_in_ms, t_out_ms, side):
    """сумма ставок меток t in (t_in, t_out]; лонг платит ставку, шорт получает: возврат стоимость в bps (+ = расход)."""
    m = (times > t_in_ms) & (times <= t_out_ms)
    return float(side) * 1e4 * float(rates[m].sum())


if __name__ == "__main__":
    d = funding_month(sys.argv[1], sys.argv[2], sys.argv[3])
    print(len(d), d.head(3).to_dict("records"))
