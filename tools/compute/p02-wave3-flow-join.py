#!/usr/bin/env python3
"""П-02, третья очередь: Г-46 (расхождение знака потока тейкеров CVD в окне
до подхода и направления цены) — на лету, без хранения сырой ленты сделок.

Не калибровка: окна (60с, 300с) заданы протоколом заранее (§7/H5), здесь
только структурное сравнение знаков — ничего не подбирается по исходу.
`ended_by_death` читается только для итоговой компактной строки (метка
исхода переносится как есть для последующего анализа блоком A стиля), не
используется для выбора окна/порога здесь.

Вход одного символа-суток:
  --trades  <lob trades --out>: day_utc,exch_ts_ms,local_ts_ms,side,price_tick,
            price_usd,qty_lots,block,rpi
  --touches <touches-<SYM>.csv из кэша D20>: обычные 61(+) колонок, читаем по
            имени start_ms,side,price_tick,touch_index,ended_by_death

Выход — одна строка на касание:
  symbol,day_utc,side,price_tick,touch_index,ended_by_death,
  cvd60,pricedelta60,mismatch60,cvd300,pricedelta300,mismatch300

mismatch<W> = 1, если знак(cvd) и знак(pricedelta) противоположны (оба не
нулевые); 0 — совпадают; "na" — один из знаков нулевой (нет сделок в окне,
либо цена не двигалась) — это не мусор, а отдельная категория ("нейтрально"),
не смешивать с "match" при подсчёте долей.

Использование (один символ-сутки, вызывается из шелл-обёртки на каждую пару):
    python3 p02-wave3-flow-join.py --symbol CRVUSDT --day-utc 2026-08-01 \
        --trades /tmp/trades.csv --touches study/approaches/D20/2026-08-01/touches-CRVUSDT.csv \
        --out /tmp/compact-CRVUSDT.csv --windows-ms 60000,300000
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import sys
from typing import List, Tuple


def sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def load_trades(path: str, ts_col: str = "exch_ts_ms") -> Tuple[List[int], List[int], List[int]]:
    """Возвращает (ts_ms отсортирован, знак сделки +qty/-qty, price_tick) —
    списки одинаковой длины, отсортированные по ts_ms (лента и так в порядке
    записи, но сортируем явно — дешёвая гарантия)."""
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ts = int(row[ts_col])
                side = row["side"].strip().lower()
                qty = float(row["qty_lots"])
                price = int(row["price_tick"])
            except (ValueError, KeyError):
                continue
            signed_qty = qty if side == "buy" else -qty
            rows.append((ts, signed_qty, price))
    rows.sort(key=lambda t: t[0])
    ts_list = [r[0] for r in rows]
    qty_list = [r[1] for r in rows]
    price_list = [r[2] for r in rows]
    return ts_list, qty_list, price_list


def window_features(ts_list, qty_list, price_list, start_ms: int, window_ms: int, end_gap_ms: int = 0):
    """CVD и дельта цены в [start_ms - end_gap_ms - window_ms, start_ms - end_gap_ms) по ленте
    сделок того же символа-суток. Дельта цены — последняя цена в окне минус первая
    цена в окне (сделками; если сделок < 2, дельта = 0 -> 'na'). `end_gap_ms` > 0 — проверка
    чистоты во времени: сделки последних мс до касания (само касание, рассинхрон книги и ленты)
    в окно не идут; умолчание 0 — заморожённое окно."""
    end = start_ms - end_gap_ms
    lo = end - window_ms
    i0 = bisect.bisect_left(ts_list, lo)
    i1 = bisect.bisect_left(ts_list, end)
    if i1 <= i0:
        return 0.0, 0
    cvd = sum(qty_list[i0:i1])
    price_delta = price_list[i1 - 1] - price_list[i0]
    return cvd, price_delta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", required=True)
    p.add_argument("--day-utc", required=True)
    p.add_argument("--trades", required=True)
    p.add_argument("--touches", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--windows-ms", default="60000,300000")
    # проверка чистоты во времени (26.09): умолчания — заморожённое поведение; обёртка
    # пересчёта передаёт их через окружение, не меняя своей командной строки
    p.add_argument("--end-gap-ms", type=int, default=int(os.environ.get("P02_END_GAP_MS", "0")))
    p.add_argument("--ts-col", default=os.environ.get("P02_TS_COL", "exch_ts_ms"),
                   choices=("exch_ts_ms", "local_ts_ms"))
    args = p.parse_args(argv)

    windows = [int(w) for w in args.windows_ms.split(",")]
    ts_list, qty_list, price_list = load_trades(args.trades, args.ts_col)

    with open(args.touches, encoding="utf-8", newline="") as f, open(
        args.out, "a", encoding="utf-8", newline=""
    ) as out:
        r = csv.DictReader(f)
        for row in r:
            if row.get("day_utc", "").startswith("#"):
                continue
            try:
                start_ms = int(row["start_ms"])
                side = row["side"]
                price_tick = row["price_tick"]
                touch_index = row["touch_index"]
                ended = row["ended_by_death"]
            except (KeyError, ValueError):
                continue
            fields = [args.symbol, args.day_utc, side, price_tick, touch_index, ended]
            for w in windows:
                cvd, pdelta = window_features(ts_list, qty_list, price_list, start_ms, w, args.end_gap_ms)
                s_cvd, s_price = sign(cvd), sign(pdelta)
                if s_cvd == 0 or s_price == 0:
                    mismatch = "na"
                elif s_cvd != s_price:
                    mismatch = "1"
                else:
                    mismatch = "0"
                fields.append(mismatch)
            out.write(",".join(fields) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
