"""TK-609: золотой набор numpy/pandas для `src/breakout/feat` — выходы numpy 2.2.4 / pandas 2.2.3 на детерминированных данных.

Запуск (srv): python3 tools/p42/np_golden.py src/breakout/feat/golden_np.json
Данные — splitmix64 (воспроизводятся в Rust без файлов): state_i = seed + (i+1)·0x9E3779B97F4A7C15, mix 0xBF58476D1CE4E5B9, 0x94D049BB133111EB,
u = (z>>11)·2⁻⁵³, e = (z & 63) − 32, x = (u − 0.3)·2^e (знакопеременные), для log — (1 + u)·2^e. Результаты — биты (hex u64);
массивы — FNV-1a64 по битам. Тест Rust: `feat/tests.rs::golden_*`.
"""
from __future__ import annotations

import io
import json
import random
import sys

import numpy as np
import pandas as pd

M = 0xFFFFFFFFFFFFFFFF
GAMMA = np.uint64(0x9E3779B97F4A7C15)
C1 = np.uint64(0xBF58476D1CE4E5B9)
C2 = np.uint64(0x94D049BB133111EB)


def mix(seed: int, n: int):
    i = np.arange(1, n + 1, dtype=np.uint64)
    z = np.uint64(seed & M) + i * GAMMA
    z = (z ^ (z >> np.uint64(30))) * C1
    z = (z ^ (z >> np.uint64(27))) * C2
    z = z ^ (z >> np.uint64(31))
    u = (z >> np.uint64(11)).astype(np.float64) * 2.0 ** -53
    e = (z & np.uint64(63)).astype(np.int64) - 32
    return u, np.ldexp(1.0, e)


def gen(seed: int, n: int) -> np.ndarray:
    u, p = mix(seed, n)
    return (u - 0.3) * p


def gen_pos(seed: int, n: int) -> np.ndarray:
    u, p = mix(seed, n)
    return (1.0 + u) * p


def bits(x: float) -> str:
    return format(int(np.float64(x).view(np.uint64)), "016x")


def fnv(a: np.ndarray) -> str:
    h = 0xCBF29CE484222325
    for w in np.ascontiguousarray(a).view(np.uint64).tolist():
        for k in range(8):
            h = ((h ^ ((w >> (8 * k)) & 0xFF)) * 0x100000001B3) & M
    return format(h, "016x")


def lengths():
    ls = list(range(0, 301)) + list(range(511, 1026)) + [1500, 2047, 2048, 2049, 4095, 4096, 4097, 4099, 8191, 8192, 8193, 8194, 16383, 16384, 16385, 24577,
                                                       32768, 65537, 100003, 262145, 1_000_000]
    rnd = random.Random(7)
    ls += [rnd.randrange(1000, 300_000) for _ in range(60)]
    return sorted(set(ls))


def main():
    out = {"numpy": np.__version__, "pandas": pd.__version__}
    # np.sum / np.mean по срезу [off : off+n] массива длиной off+n
    sums, means = [], []
    for n in lengths():
        for off in (0, 3):
            seed = n * 7919 + off * 104729 + 1
            a = gen(seed, off + n)[off:]
            sums.append([seed, n, off, bits(np.sum(a))])
            if n > 0:
                means.append([seed, n, off, bits(np.mean(a))])
    out["sum"] = sums
    out["mean"] = means
    # маскированные суммы (как v[b].sum()): булева маска из младших битов другого генератора
    masked = []
    for n in (1, 7, 8, 9, 127, 128, 129, 1000, 8193, 50_001):
        seed = n * 31 + 5
        a = gen(seed, n)
        u, _ = mix(seed + 99, n)
        m = u < 0.5
        masked.append([seed, n, bits(np.sum(a[m]))])
    out["masked_sum"] = masked
    # cumsum / log / log10 — хэши
    cs, lg, l10 = [], [], []
    for n in (0, 1, 2, 7, 8, 100, 1000, 8192, 8193, 30_001):
        seed = n * 13 + 3
        a = gen(seed, n)
        cs.append([seed, n, fnv(np.cumsum(a))])
        p = gen_pos(seed, n)
        lg.append([seed, n, fnv(np.log(p))])
        l10.append([seed, n, fnv(np.log10(p))])
    out["cumsum"], out["log"], out["log10"] = cs, lg, l10
    # math.log / math.log10 (то, что зовёт tk594_feat) на тех же числах
    import math
    p = gen_pos(1234, 5000)
    out["math_log"] = fnv(np.array([math.log(float(x)) for x in p]))
    out["math_log10"] = fnv(np.array([math.log10(float(x)) for x in p]))
    # границы корзин
    edges = np.array([10.0 ** ((j - 300) / 100.0) for j in range(1201)])
    out["edges"] = fnv(edges)
    out["edges_arr_pow"] = fnv(np.array([float(np.float64(10.0) ** np.float64((j - 300) / 100.0)) for j in range(1201)]))
    # searchsorted (обе стороны) на отсортированном массиве; запросы — другой генератор, включая точные значения
    ss = []
    for na, nx in ((1, 50), (100, 1000), (5000, 20000), (100000, 50000)):
        seed = na * 17 + 11
        a = np.sort(gen(seed, na))
        x = gen(seed + 1, nx)
        x[: min(na, nx) // 2] = a[: min(na, nx) // 2]  # точные совпадения
        for side in ("left", "right"):
            r = np.searchsorted(a, x, side).astype(np.int64)
            ss.append([seed, na, nx, side, fnv(r.view(np.uint64))])
    out["searchsorted"] = ss
    # гистограммный индекс: ix = clip(searchsorted(EDGES, v, 'right') - 1, 0, NB - 1) для v = |x| со всех масштабов
    v = np.abs(gen(555, 200_000))
    v = np.concatenate([v, edges, edges * (1 + 2 ** -52), edges * (1 - 2 ** -53)])
    ix = np.clip(np.searchsorted(edges, v, "right") - 1, 0, 1199).astype(np.int64)
    out["hist_index"] = fnv(ix.view(np.uint64))
    # xstrtod: pandas.read_csv по умолчанию (float_precision=None → precise_xstrtod), dtype float64
    rnd = random.Random(2026)
    strs = ["0", "-0", "-0.0", "5", ".5", "5.", "+7.25", "9.16E-4", "9.16e-4", "1e308", "1e-300", "1e-320", "123456789012345678901234567890",
            "0.000000000000000000001", "13052463.0000000000000000", "0.10000000000000000555", "1.7976931348623157e308", "2.2250738585072014e-308",
            "4.9406564584124654e-324", "9007199254740993", "9007199254740993.0", "0.1", "0.2", "0.3", "1.1", "3.14159265358979323846"]
    for _ in range(700):
        nd = rnd.randrange(1, 26)
        digs = "".join(rnd.choice("0123456789") for _ in range(nd))
        if rnd.random() < 0.3:
            digs = "0" * rnd.randrange(0, 4) + digs
        k = rnd.randrange(0, len(digs) + 1)
        s = digs[:k] + ("." if rnd.random() < 0.9 else "") + digs[k:]
        if s in ("", ".", "+", "-") or not any(c.isdigit() for c in s):
            continue
        r = rnd.random()
        if r < 0.15:
            s += rnd.choice(["e", "E"]) + rnd.choice(["", "+", "-"]) + str(rnd.randrange(0, 40))
        if rnd.random() < 0.2:
            s = rnd.choice(["-", "+"]) + s
        strs.append(s)
    ok = []
    for s in strs:  # каждая строка отдельно: pandas разбирает как есть, непригодные (overflow и т. п.) отбрасываем
        try:
            df = pd.read_csv(io.StringIO("x\n" + s + "\n"), dtype={"x": np.float64})
            ok.append([s, bits(df["x"].to_numpy()[0])])
        except Exception:
            pass
    out["xstrtod"] = ok
    # те же строки одним столбцом — проверка, что разбор не зависит от соседей
    df = pd.read_csv(io.StringIO("x\n" + "\n".join(s for s, _ in ok) + "\n"), dtype={"x": np.float64})
    assert [bits(x) for x in df["x"].to_numpy()] == [b for _, b in ok]
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})


if __name__ == "__main__":
    main()
