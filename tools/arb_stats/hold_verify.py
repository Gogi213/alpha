"""Независимая сверка счёта эпизодов: другой способ — сетка 1 с (состояние каждой ноги на каждой целой секунде), серии подряд идущих секунд с условием «от нуля» — против hold_scan (по событиям).

Запуск: python3 hold_verify.py <table.pkl> <книги (с parsed/)> монета:биржаA:биржаB[,…]
Ожидание: эпизод длительностью ≥ N с содержит ≥ N целых секунд, серия из N секунд лежит в эпизоде длительностью > N − 1 с: число серий ≥ N на сетке между числом эпизодов ≥ N с и ≥ N − 1 с (сливаются лишь эпизоды, разделённые меньше секунды).
"""
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker_run  # noqa: E402
from hold_common import build_coins, unit_of  # noqa: E402
from hold_scan import CI, runs_of  # noqa: E402


def grid_runs(A, B, t0, t1, th, side):
    """Серии подряд идущих целых секунд, где Sell ≥ th (side = 1) или −Buy ≥ th (side = −1); состояние — последнее изменение не позже секунды. -> длительности серий в секундах."""
    g = np.arange((t0 // 1000 + 1) * 1000, t1, 1000)
    ia, ib = np.searchsorted(A["e"], g, "right") - 1, np.searchsorted(B["e"], g, "right") - 1
    ok = (ia >= 0) & (ib >= 0)
    ia, ib = np.maximum(ia, 0), np.maximum(ib, 0)
    e = (A["bid"][ia] / B["ask"][ib] - 1) * 1e4 if side == 1 else -(A["ask"][ia] / B["bid"][ib] - 1) * 1e4
    st, en = runs_of(ok & (e >= th))
    return (en - st + 1).astype(int)


def main(tabp, bwork, spec):
    tab = pickle.load(open(tabp, "rb"))
    df = tab["df"]
    coins, _ = build_coins(str(Path(bwork, "parsed")), ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"])
    for item in spec.split(","):
        coin, a, b = item.split(":")
        books = {}
        for ex in (a, b):
            sym, mult, meta = coins[coin][ex]
            books[ex] = maker_run.load_book(str(Path(bwork, "parsed")), ex, sym, mult, unit_of(ex, meta))
        for th in (50.0, 100.0):
            sub = df[(df["coin"] == coin) & (df["exA"] == a) & (df["exB"] == b) & (df["th"] == th) & (df["view"] == "z")]
            lens = np.concatenate([grid_runs(books[a], books[b], tab["t0"], tab["t1"], th, s) for s in (1, -1)])
            row = []
            for n in (5, 10, 30, 60):
                lo, hi = int((sub["dur"] >= n * 1000).sum()), int((sub["dur"] >= (n - 1) * 1000).sum())
                gr = int((lens >= n).sum())
                row.append(f"≥{n} с: сетка {gr} (ожидание между {lo} [события ≥ {n} с] и {hi} [≥ {n - 1} с])")
            print(coin, a, b, th, " | ".join(row))


if __name__ == "__main__":
    main(*sys.argv[1:4])
