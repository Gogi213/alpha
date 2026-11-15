"""Пропуски сбора по биржам: события книг самых активных символов биржи (K = 8 с наибольшим объёмом кусков в <parsed>/<биржа>/ — у Binance в пуле нет BTC/ETH, пул ротируется по дням) — интервалы без единой записи по всем K символам ≥ G секунд.

Запуск: python3 gaps_scan.py <каталог parsed книг> <t0_мс> <t1_мс> <выход.json> [G_с=15]
Пишет {биржа: [[начало_мс, конец_мс], …]} (время E биржи) и печатает сводку: число пропусков, суммарная длина, доля окна, самые длинные. Файл читает outlier_run.py (переменная DEAD).
Что видно по определению: перезапуск сборщика (разрыв между запусками 0–50 с), обрыв соединения, остановка записи. Пропуск короче G секунд (пересинхронизация после обрыва) не виден.
"""
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import EXCHANGES  # noqa: E402

TOPK = 8


def liquid(parsed, ex, k=TOPK):
    """k самых активных символов биржи в каталоге разобранных книг (по суммарному размеру кусков)."""
    size = {}
    for f in glob.glob(str(Path(parsed, ex, "*.npz"))):
        sym = Path(f).name.split(".")[0]
        size[sym] = size.get(sym, 0) + Path(f).stat().st_size
    return sorted(size, key=lambda s: -size[s])[:k]


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d.%m %H:%M:%S")


def main(parsed, t0, t1, out, g_s=15):
    res = {}
    for ex in EXCHANGES:
        syms = liquid(parsed, ex)
        ev = []
        for s in syms:
            for f in glob.glob(str(Path(parsed, ex, s + ".*.npz"))):
                ev.append(np.load(f)["e"])
        if not ev:
            print(ex, "нет данных")
            res[ex] = [[t0, t1]]
            continue
        e = np.sort(np.concatenate(ev))
        e = e[(e >= t0 - 3_600_000) & (e <= t1)]
        first, last = int(e[0]), int(e[-1])
        d = np.diff(e)
        i = np.flatnonzero(d > g_s * 1000)
        gaps = [[int(e[k]), int(e[k + 1])] for k in i]
        if first > t0:
            gaps.insert(0, [t0, first])         # данных ещё нет
        if last < t1:
            gaps.append([last, t1])
        gaps = [[max(a, t0), min(b, t1)] for a, b in gaps if b > t0 and a < t1]
        res[ex] = gaps
        tot = sum(b - a for a, b in gaps) / 1000
        top = sorted(gaps, key=lambda x: x[0] - x[1])[:6]
        print(f"{ex}: символов {len(syms)}, первая запись {fmt(first)}, последняя {fmt(last)}, пропусков ≥ {g_s} с: {len(gaps)}, всего {tot:.0f} с ({tot * 1000 / (t1 - t0) * 100:.3f} % окна)")
        for a, b in top:
            print(f"    {fmt(a)} → {fmt(b)}  ({(b - a) / 1000:.0f} с)")
    Path(out).write_text(json.dumps(res), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 15)
