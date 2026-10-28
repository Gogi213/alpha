#!/usr/bin/env python3
"""TK-439: детектор окон TK-435 с numba-ядром вместо moves()+starts(): скользящие min/max окна старта [j−D, j−MIN_DUR] и индекс первого
вхождения экстремума — одним проходом O(n) монотонной очередью (было: ван Херк + argmin по окну на каждого кандидата, O(|js|·L)).
Вывод побайтно тот же: формулы up/dn, отбор «bad» и жадный проход — из tk435-windows.py без правок (импортируется как модуль).
  <venv с numba>/python tk439-windows.py events … (аргументы как у tk435-windows.py); NUMBA_CACHE_DIR — каталог кэша компиляции."""
import importlib.util
import os
import sys

import numpy as np
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("tk435w", os.path.join(HERE, "tk435-windows.py"))
w = importlib.util.module_from_spec(sp)
sys.modules["tk435w"] = w
sp.loader.exec_module(w)
_ST = {}


@njit(cache=True)
def _win(x, W, md, sgn):
    """idx[j] = первый индекс экстремума x (min при sgn>0, max при sgn<0) в [max(0, j−W), j−md]; −1, если j−md < 0."""
    n = len(x)
    idx = np.full(n, -1, np.int64)
    dq = np.empty(n, np.int64)
    head = 0
    tail = 0
    for j in range(n):
        e = j - md
        if e < 0:
            continue
        while tail > head and ((x[dq[tail - 1]] > x[e]) if sgn > 0 else (x[dq[tail - 1]] < x[e])):
            tail -= 1
        dq[tail] = e
        tail += 1
        while dq[head] < j - W:
            head += 1
        idx[j] = dq[head]
    return idx


def moves(cc, W):
    """то же, что tk435-windows.moves; индексы старта запоминаются для starts()."""
    lo = np.where(np.isnan(cc), np.inf, cc)
    hi = np.where(np.isnan(cc), -np.inf, cc)
    iu = _win(lo, W, w.MIN_DUR, 1)
    idn = _win(hi, W, w.MIN_DUR, -1)
    _ST[(W, 1)], _ST[(W, -1)] = iu, idn
    mn = np.where(iu >= 0, lo[np.maximum(iu, 0)], np.inf)
    mx = np.where(idn >= 0, hi[np.maximum(idn, 0)], -np.inf)
    bad = np.isnan(cc) | ~np.isfinite(mn)
    with np.errstate(invalid="ignore"):
        up = np.where(bad, np.nan, np.maximum(cc / mn - 1, 0))
        dn = np.where(bad, np.nan, np.maximum(1 - cc / mx, 0))
    return up, dn, lo, hi


def starts(src, js, W, sgn):
    return _ST[(W, sgn)][js]


w.moves, w.starts = moves, starts

if __name__ == "__main__":
    _win(np.zeros(4), 2, 1, 1)             # компиляция/загрузка кэша до fork
    w.main()
