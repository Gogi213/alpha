#!/usr/bin/env python3
"""Боковая таблица σ монеты на взводе для σ-лестницы (В-131, Судья 27.09 «Окончательно»,
`docs/research/reviews/v131-sigma-geometry-2026-09-27.md`) → `lob bounce-grid --sigma-from <out-dir>`.

    python3 tools/compute/sigma-table.py --klines epochs/e-aug/study/klines --klines study/klines \\
            [--klines study/klines-0831] --out-dir study/sigma240 [--symbols AAVEUSDT,…]

σ = √Σ r² по 1-минутным лог-доходностям закрытий за 240 минут, в bps. Окно кончается на последней
**закрытой** минуте до сигнала: строка `window_end_ms = E` (кратно минуте) — окно из минут с открытием
`E − 240 мин … E − 1 мин`; `bounce-grid` берёт `E = ⌊arm_ms / 1 мин⌋ · 1 мин` (минута, закрывшаяся ровно в
`arm_ms`, уже закрыта). Доходность минуты `m` — `ln(C_m / C_{m−1})`, только если есть обе свечи подряд
(дыра в свечах не даёт доходности ни себе, ни следующей минуте — ход через дыру не прячется в одну «минуту»).
Меньше 200 доходностей из 240 — строки нет: σ нет, сигнал не торгуется (`n_no_sigma` у сетки).

Свечи — `ref-<SYMBOL>-1m.csv` (`minute_ms,…,close,…`, `tools/compute/ref-klines.py`) из всех `--klines`
по очереди; одна минута в двух каталогах — закрытие обязано совпасть (иначе отказ). Пишет
`<out-dir>/sigma-<SYMBOL>.csv` (`window_end_ms,sigma_bps`, σ с 6 знаками) — детерминированно, повтор даёт
те же байты; монета без свечей — отказ (а не пустой файл: сетка упала бы на σ-форме молча).
"""
import argparse
import csv
import math
import os
import sys

MINUTE_MS = 60_000
WINDOW_MIN = 240  # В-131: окно σ, минут
MIN_RETURNS = 200  # В-131: меньше — σ нет


def read_closes(dirs, symbol):
    closes = {}
    for d in dirs:
        path = os.path.join(d, f"ref-{symbol}-1m.csv")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m = int(r["minute_ms"])
                c = r["close"]
                prev = closes.get(m)
                if prev is not None and float(prev) != float(c):
                    raise SystemExit(f"{symbol}: минута {m} — закрытие {prev} и {c} в разных каталогах")
                closes[m] = c
    return closes


def sigma_rows(closes):
    """(window_end_ms, sigma_bps) для каждого конца окна, где доходностей ≥ MIN_RETURNS."""
    if not closes:
        return []
    minutes = sorted(closes)
    m0 = minutes[0]
    n = (minutes[-1] - m0) // MINUTE_MS + 1
    sq = [0.0] * n
    ok = [0] * n
    for m in minutes:
        prev = closes.get(m - MINUTE_MS)
        if prev is None:
            continue
        c, p = float(closes[m]), float(prev)
        if c <= 0 or p <= 0:
            raise SystemExit(f"минута {m}: цена ≤ 0")
        i = (m - m0) // MINUTE_MS
        sq[i] = math.log(c / p) ** 2
        ok[i] = 1
    # префиксные суммы: окно минут [e − 240, e − 1] → S[e] − S[e − 240]
    ps = [0.0] * (n + 1)
    pc = [0] * (n + 1)
    for i in range(n):
        ps[i + 1] = ps[i] + sq[i]
        pc[i + 1] = pc[i] + ok[i]
    rows = []
    for e in range(MIN_RETURNS, n + 1):
        lo = max(e - WINDOW_MIN, 0)  # до первой свечи доходностей нет — окно просто короче
        cnt = pc[e] - pc[lo]
        if cnt < MIN_RETURNS:
            continue
        s = max(ps[e] - ps[lo], 0.0)
        rows.append((m0 + e * MINUTE_MS, math.sqrt(s) * 10_000))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", action="append", required=True, help="каталог ref-<SYMBOL>-1m.csv (повторяемый)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--symbols", help="через запятую; по умолчанию — все монеты каталогов")
    a = ap.parse_args()
    if a.symbols:
        symbols = a.symbols.split(",")
    else:
        symbols = sorted({f[4:-7] for d in a.klines for f in os.listdir(d)
                          if f.startswith("ref-") and f.endswith("-1m.csv")})
    os.makedirs(a.out_dir, exist_ok=True)
    for symbol in symbols:
        closes = read_closes(a.klines, symbol)
        if not closes:
            raise SystemExit(f"{symbol}: свечей нет ни в одном --klines")
        rows = sigma_rows(closes)
        path = os.path.join(a.out_dir, f"sigma-{symbol}.csv")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("window_end_ms,sigma_bps\n")
            for end, s in rows:
                f.write(f"{end},{s:.6f}\n")
        os.replace(tmp, path)
        med = sorted(s for _, s in rows)[len(rows) // 2] if rows else float("nan")
        print(f"{symbol}: свечей {len(closes)}, окон с σ {len(rows)}, медиана σ {med:.1f} bps → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
