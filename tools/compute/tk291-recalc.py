#!/usr/bin/env python3
"""TK-291 (П-19 §6): линейный пересчёт портфеля по вектору k = (k0,k1,k2,k3) на деревьях B1 со столбцом touch_index.
tk291-recalc.py <r-b дом> <мес> <выход.json> [--vec метка=k0,k1,k2,k3 …] [--cap N]
Без --vec — B1 (1,1,1,1) и сетка П-19 §3: ось a∈{0..3} × k∈{0, 1/4, 3/8, 1/2, 3/4, 3/2, 2, 3, 4, 6, 8} = 44 клетки.
Выход: {метка: [[мс закрытия, $, монета, нотионал $], …]} — сделки, которые счёт взял (cap 0 — без потолка позиций; TRUMP/TRX/BCH — вне пула, В-210).
Нормировка m и сито — tk291-sieve.py. Деревья r-b/<мес>/<сутки>/t-bid-btc4h-q1/rounds.csv — после busy-replay (tk291-recalc.sh)."""
import importlib.util
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("_lib", os.path.join(here, "_lib", "__init__.py"))
_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_lib)
P = _lib._portfolio
FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SET = "t-bid-btc4h-q1"
DROP = {"TRUMPUSDT", "TRXUSDT", "BCHUSDT"}
K = [0, 0.25, 0.375, 0.5, 0.75, 1.5, 2, 3, 4, 6, 8]


def grid():
    v = {"B1": (1.0, 1.0, 1.0, 1.0)}
    for a in range(4):
        for k in K:
            t = [1.0] * 4
            t[a] = float(k)
            v[f"a{a}k{k:g}"] = tuple(t)
    return v


def main():
    home, mo, out = sys.argv[1:4]
    a = sys.argv[4:]
    cap = 0
    if "--cap" in a:
        i = a.index("--cap")
        cap = int(a[i + 1])
        del a[i:i + 2]
    vec = {}
    while "--vec" in a:
        i = a.index("--vec")
        lab, ks = a[i + 1].split("=")
        vec[lab] = tuple(float(x) for x in ks.split(","))
        del a[i:i + 2]
    vec = vec or grid()
    btc = P.load_btc1h(home)
    klines = P.Klines([])
    res = {}
    for lab, k in vec.items():
        P.LOT_BY_N = k
        rows = [r for r in P.load_rounds(home, mo, SET, FORM) if r["sym"] not in DROP]
        r = P.simulate(rows, btc, klines, 2500.0, cap, 0.0, 0.0, set(), 59.7, 0, None)
        usd = {(t1 // 1_000_000, x["sym"]): x["usd"] for x in rows for t1 in (x["t1"],)}
        res[lab] = [[ms, p, sym, round(usd[(ms, sym)], 2)] for ms, p, sym in r["_closes_sym"]]
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(mo, len(res), "векторов →", out, file=sys.stderr)


if __name__ == "__main__":
    main()
