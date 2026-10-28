"""TK-441 круг 3: микро-замер способов для corr/fractals на реальных вызовах UAIUSDT (запись из new.py, pickle).
A — текущий python (списки); B — numba, list->ndarray на вызов; C — numba, готовые ndarray (путь «колонки куба»);
D — corr: одним numba-вызовом по всем вызовам (рваный массив, векторно по окнам). Сверка побитно с A.
usage: tk441-arrbench.py rec.pkl"""
import math, pickle, sys, time
import numpy as np
from numba import njit

R = pickle.load(open(sys.argv[1], "rb"))
CC, FF = R["corr"], R["fr"]


def corr(x, y):
    n = len(x)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    dx = [a - mx for a in x]
    dy = [b - my for b in y]
    sxx = sum([a ** 2 for a in dx])
    syy = sum([b ** 2 for b in dy])
    if sxx <= 0 or syy <= 0:
        return None
    return sum([a * b for a, b in zip(dx, dy)]) / math.sqrt(sxx * syy)


def fractals(xs, k, hi):
    out = []
    for i in range(k, len(xs) - k):
        x = xs[i]
        if hi:
            ok = all(x > y for y in xs[i - k : i]) and all(x > y for y in xs[i + 1 : i + k + 1])
        else:
            ok = all(x < y for y in xs[i - k : i]) and all(x < y for y in xs[i + 1 : i + k + 1])
        if ok:
            out.append(i)
    return out


@njit(cache=False)
def nsum(a, lo, hi):
    # CPython 3.13 sum(float): первый элемент 0.0 + x, далее Neumaier, в конце + c (если c != 0 и конечно)
    f = 0.0 + a[lo]
    c = 0.0
    for i in range(lo + 1, hi):
        x = a[i]
        t = f + x
        if abs(f) >= abs(x):
            c += (f - t) + x
        else:
            c += (x - t) + f
        f = t
    if c != 0.0 and math.isfinite(c):
        f += c
    return f


@njit(cache=False)
def ncorr1(x, y):
    n = x.shape[0]
    if n < 5:
        return np.nan
    mx = nsum(x, 0, n) / n
    my = nsum(y, 0, n) / n
    dx = np.empty(n)
    dy = np.empty(n)
    for i in range(n):
        dx[i] = x[i] - mx
        dy[i] = y[i] - my
    q = np.empty(n)
    for i in range(n):
        q[i] = dx[i] * dx[i]
    sxx = nsum(q, 0, n)
    for i in range(n):
        q[i] = dy[i] * dy[i]
    syy = nsum(q, 0, n)
    if sxx <= 0 or syy <= 0:
        return np.nan
    for i in range(n):
        q[i] = dx[i] * dy[i]
    return nsum(q, 0, n) / math.sqrt(sxx * syy)


@njit(cache=False)
def ncorr_all(X, Y, off):
    m = off.shape[0] - 1
    out = np.empty(m)
    for j in range(m):
        out[j] = ncorr1(X[off[j]:off[j + 1]], Y[off[j]:off[j + 1]])
    return out


@njit(cache=False)
def nfr(xs, k, hi):
    n = xs.shape[0]
    out = []
    for i in range(k, n - k):
        x = xs[i]
        ok = True
        for j in range(i - k, i):
            if hi:
                if not (x > xs[j]):
                    ok = False
                    break
            else:
                if not (x < xs[j]):
                    ok = False
                    break
        if ok:
            for j in range(i + 1, i + k + 1):
                if hi:
                    if not (x > xs[j]):
                        ok = False
                        break
                else:
                    if not (x < xs[j]):
                        ok = False
                        break
        if ok:
            out.append(i)
    return out


def bits(v):
    return None if v is None or v != v else np.float64(v).view(np.int64).item()


def t(f):
    s = time.perf_counter()
    r = f()
    return time.perf_counter() - s, r


# прогрев JIT (не в замере)
ncorr1(np.arange(6.0), np.arange(6.0) ** 2)
ncorr_all(np.arange(6.0), np.arange(6.0), np.array([0, 6]))
nfr(np.arange(6.0), 1, True)
print("corr: вызовов %d, средняя длина %.1f" % (len(CC), sum(len(x) for x, _ in CC) / max(1, len(CC))))
tA, rA = t(lambda: [corr(x, y) for x, y in CC])
tB, rB = t(lambda: [ncorr1(np.array(x), np.array(y)) for x, y in CC])
AX = [(np.array(x), np.array(y)) for x, y in CC]
tC, rC = t(lambda: [ncorr1(x, y) for x, y in AX])
X = np.concatenate([a for a, _ in AX]); Y = np.concatenate([b for _, b in AX])
off = np.cumsum([0] + [len(a) for a, _ in AX]).astype(np.int64)
tD, rD = t(lambda: ncorr_all(X, Y, off))
bA = [bits(v) for v in rA]
for nm, tt, r in (("B list->ndarray на вызов", tB, rB), ("C готовые ndarray", tC, rC), ("D один вызов, рваный массив", tD, rD)):
    bad = sum(1 for a, b in zip(bA, [bits(v) for v in r]) if a != b)
    print("corr %-28s %.4f с  (A %.4f с, ×%.2f)  побитно ≠ A: %d из %d" % (nm, tt, tA, tA / tt if tt else 0, bad, len(bA)))
print("fractals: вызовов %d, средняя длина %.1f" % (len(FF), sum(len(x) for x, _, _ in FF) / max(1, len(FF))))
tFA, fA = t(lambda: [fractals(x, k, hi) for x, k, hi in FF])
tFB, fB = t(lambda: [list(nfr(np.array(x), k, hi)) for x, k, hi in FF])
FX = [(np.array(x), k, hi) for x, k, hi in FF]
tFC, fC = t(lambda: [list(nfr(x, k, hi)) for x, k, hi in FX])
for nm, tt, r in (("B list->ndarray на вызов", tFB, fB), ("C готовые ndarray", tFC, fC)):
    print("fractals %-26s %.4f с  (A %.4f с, ×%.2f)  ≠ A: %d из %d" % (nm, tt, tFA, tFA / tt if tt else 0, sum(1 for a, b in zip(fA, r) if a != b), len(fA)))
