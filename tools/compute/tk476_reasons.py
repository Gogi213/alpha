#!/usr/bin/env python3
"""TK-476: coverage2.csv + список «нет в архиве» (cl.absent) -> итоговая таблица с колонками «источник» и «причина».

Запуск: python tk476_reasons.py <coverage2.csv> <cl.absent> <closed-syms.txt> <выход.csv>
Ничего не выдумывает: причина собирается из чисел coverage и числа суток «нет в архиве Bybit» (403) по монете.
"""
import csv
import sys
from collections import Counter

LAST_MIN = "2026-10-31 23:59"
SOURCE = {
    "binance-um": "data.binance.vision klines (месячные архивы) + REST",
    "bybit-linear": "REST /v5/market/kline",
    "gate-futures_usdt": "архив сделок gatedata (свечи из сделок)",
}
CLOSED_SOURCE = "архив сделок public.bybit.com/trading (свечи по правилам REST: open = close пред. минуты)"


def reason(r, closed, absent):
    ex, n = r["биржа"], int(r["строк"])
    if n == 0:
        return f"нет данных: в архиве Bybit нет файлов за все {absent} суток листинга (403), REST пуст (монета снята)"
    parts = []
    gaps, miss = int(r["дыр"]), int(r["пропущено_минут"])
    if gaps:
        if ex == "gate-futures_usdt" or closed:
            parts.append(f"минуты без сделок не выдумывались ({gaps} дыр, {miss} мин)")
        else:
            parts.append(f"дыры биржи/выкладки ({gaps} дыр, {miss} мин; Binance 28/30.10 — TK-453)")
    if closed and absent:
        parts.append(f"нет в архиве Bybit {absent} суток (403)")
    if r["последняя_utc"] < LAST_MIN:
        parts.append(f"после {r['последняя_utc']} данных нет (снята, не торговалась или архив биржи не выложен)")
    return "; ".join(parts) if parts else "дыр нет"


def main(cov, absent_file, closed_file, out):
    absent = Counter(l.split()[0] for l in open(absent_file, encoding="utf-8") if l.strip())
    closed = set(open(closed_file, encoding="utf-8").read().split())
    rows = list(csv.DictReader(open(cov, encoding="utf-8")))
    cols = [c for c in rows[0] if c != "ошибка"] + ["источник", "причина"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            c = r["биржа"] == "bybit-linear" and r["символ"] in closed
            a = absent[r["символ"]] if c else 0
            r["источник"] = CLOSED_SOURCE if c else SOURCE[r["биржа"]]
            r["причина"] = reason(r, c, a)
            w.writerow([r[k] for k in cols])


if __name__ == "__main__":
    main(*sys.argv[1:5])
