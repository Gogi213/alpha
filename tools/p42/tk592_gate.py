"""П-42 §13.18 п. 13 (TK-592): ворота — независимые реализации (контроль построчно из zip без numpy, `zv6h` и NATR «в лоб», клетки «в лоб»).

Использование: python3 tk592_gate.py ctrl <ep_u0.parquet> <выход.json> [n]      — контроль на n эпизодах (≥ 30)
               python3 tk592_gate.py zv   <ep_u0.parquet> <выход.json> [n]      — zv6h на n эпизодах (≥ 200)
               python3 tk592_gate.py label <ep_u0.parquet> <выход.json>         — метка и число пробоев
               python3 tk592_gate.py cells <ep_u0.parquet> <каталог пар> <выход.json> [n] — клетки шорт-листа «в лоб» (≥ 5)
Расхождение — ненулевой код возврата.
"""
from __future__ import annotations

import csv
import io
import json
import os
import pickle
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import BASE_MS, DAY_MS, END_MS, MIN_MS, SCALE, START_DAY, day_date, load_b42  # noqa: E402
import tk592_lib as T  # noqa: E402
from tk592_build import ctrl_candidates, rng_for  # noqa: E402  (только генератор выбора минут — случайный выбор тем же зерном)

CACHE = os.environ.get("SIGFEAT_CACHE", "/data/p42-t566/cache")
TRADES = os.environ.get("SIGFEAT_TRADES", "/data/aggtrades/binance")


def natr_brute(m, t):
    """NATR 1м/30 по минутам t−29…t (включительно) «в лоб»: среднее истинного диапазона закрытых минут с данными / close; −1 — нет значения (как в движке)."""
    o, h, l, c = m["o"], m["h"], m["l"], m["c"]
    prev_close = 0
    trs = []
    # prev_close — закрытие последней минуты с данными до минуты t−29
    for k in range(t - 29 - 1, -1, -1):
        if o[k] != 0:
            prev_close = int(c[k])
            break
    cnt = 0
    tr_sum = 0
    for k in range(t - 29, t + 1):
        if o[k] == 0:
            continue
        x = int(h[k]) - int(l[k])
        if prev_close != 0:
            x = max(x, abs(int(h[k]) - prev_close), abs(int(l[k]) - prev_close))
        tr_sum += x
        cnt += 1
        prev_close = int(c[k])
    if cnt * 2 < 30 or o[t] == 0:
        return -1
    return min((tr_sum * SCALE) // (cnt * int(c[t])), 2 ** 31 - 1)


def pool_brute(m, t):
    """Пул §13.2 по минутам ≤ t−1: NATR[t−1] ≥ 0,4 %, оборот часа (qv t−60…t−1) ≥ $500 тыс., минута t с данными."""
    if t < 61 or m["o"][t] == 0:
        return False
    n = natr_brute(m, t - 1)
    if n < 400_000:
        return False
    return sum(int(m["qv"][k]) for k in range(t - 60, t)) >= 50_000_000_000


def read_ticks(sym, day):
    """Сделки суток построчно (csv, без numpy): (время от 01.09, цена ×1e8); при убывании времени — устойчивая сортировка."""
    name = f"{sym}-aggTrades-{day_date(day)}"
    try:
        zf = zipfile.ZipFile(f"{TRADES}/{sym}/{name}.zip")
    except FileNotFoundError:
        return None
    rows = []
    with zf, zf.open(name + ".csv") as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
        head = next(rd)
        ip, it = head.index("price"), head.index("transact_time")
        for r in rd:
            rows.append((int(r[it]) - BASE_MS, int(round(float(r[ip]) * 1e8))))
    if any(rows[i][0] > rows[i + 1][0] for i in range(len(rows) - 1)):
        rows.sort(key=lambda x: x[0])
    return rows


def ticks_span(cache, sym, a, b):
    out, full = [], True
    for d in range(max(a // DAY_MS, 0), b // DAY_MS + 1):
        if d not in cache:
            cache[d] = read_ticks(sym, d)
        v = cache[d]
        if v is None:
            full = False
            continue
        out += [x for x in v if a <= x[0] <= b]
    return out, full


def outcome_brute(prices, R, x_add, f_floor):
    """O3? — первая сделка ≥ R (касание), затем: пробой P ≥ R + x раньше возврата P < R − f."""
    tc = next((i for i, p in enumerate(prices) if p >= R), None)
    if tc is None:
        return False
    tx = next((i for i, p in enumerate(prices) if p >= R + x_add), None)
    tr = next((i for i in range(tc + 1, len(prices)) if prices[i] < R - f_floor), None)
    if tx is None:
        return False
    return tr is None or tx < tr


def ceil_div(a, b):
    return -((-a) // b)


def gate_ctrl(table, out, n=40):
    e = pd.read_parquet(table)
    e = e[e.c_n >= 5].sample(n=n, random_state=592)
    bad, rows = [], []
    ms_cache = {}
    for r in e.itertuples():
        sym, s = r.sym, 1 if r.side == "long" else -1
        m = ms_cache.get(sym) or load_b42(f"{CACHE}/{sym}.b42")
        ms_cache[sym] = m
        t = r.sig_ms // MIN_MS
        day = r.sig_ms // DAY_MS
        # кандидаты «в лоб»
        lo, hi = max(day * 1440, START_DAY * 1440), (day + 1) * 1440
        inpool = bool(r.in_pool)
        cand = []
        for k in range(lo + 1, hi):
            if abs(k - t) <= 60 or abs(k - r.last_sig_ms // MIN_MS) <= 60:
                continue
            if m["o"][k] == 0 or natr_brute(m, k - 1) < 0:
                continue
            if pool_brute(m, k) != inpool:
                continue
            cand.append(k)
        cand = np.array(cand)
        # сверка с векторной версией
        from tk592_build import natr_series, pool_mask  # noqa: E402
        natr = natr_series(m)
        pool, _ = pool_mask(m, natr)
        cand_vec = ctrl_candidates(m, natr, pool, day, t, r.last_sig_ms // MIN_MS, inpool)
        if list(cand) != list(cand_vec):
            bad.append({"ep": r.ep_id, "why": "кандидаты", "brute": len(cand), "vec": len(cand_vec)})
            continue
        if len(cand) == 0:
            continue
        pick = rng_for(sym, s, int(r.ep_no), int(r.sig_ms)).choice(cand, size=min(20, len(cand)), replace=False)
        cache = {}
        n_ok = ks = kf = 0
        for mm in pick:
            a = int(mm) * MIN_MS
            b = a + 61 * MIN_MS
            if b > END_MS - 1:
                continue
            tk, full = ticks_span(cache, sym, a, b)
            if not full or not tk or tk[0][0] >= a + MIN_MS:
                continue
            P = [s * p for _, p in tk]
            P0 = P[0]
            R = P0 * SCALE // (SCALE - s * int(r.dist_e8))
            aR = abs(R)
            lim = tk[0][0] + 60 * MIN_MS
            win = [P[i] for i in range(1, len(P)) if tk[i][0] <= lim]
            x_add = ceil_div(aR * 10_000, 1_000_000)
            n_ok += 1
            ks += outcome_brute(win, R, x_add, 0)
            kf += outcome_brute(win, R, x_add, aR * 1000 // 1_000_000)
            # окно не пересекает ±60 мин вокруг сигналов эпизода
            assert abs(int(mm) - t) > 60 and abs(int(mm) - r.last_sig_ms // MIN_MS) > 60
        rec = {"ep": r.ep_id, "c_n": (int(r.c_n), n_ok), "c_s": (float(r.c_s), ks / n_ok if n_ok else None), "c_f": (float(r.c_f), kf / n_ok if n_ok else None)}
        rows.append(rec)
        if int(r.c_n) != n_ok or abs(float(r.c_s) - ks / max(n_ok, 1)) > 1e-12 or abs(float(r.c_f) - kf / max(n_ok, 1)) > 1e-12:
            bad.append(rec)
        print(rec, flush=True)
    res = {"episodes": len(rows), "mismatch": bad}
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False)[:500])
    return 1 if bad or len(rows) < 30 else 0


def gate_zv(table, out, n=250):
    e = pd.read_parquet(table).sample(n=n, random_state=592)
    bad = []
    mc = {}
    for r in e.itertuples():
        m = mc.get(r.sym) or load_b42(f"{CACHE}/{r.sym}.b42")
        mc[r.sym] = m
        t = r.sig_ms // MIN_MS
        fv = next((k for k in range(len(m["o"])) if m["o"][k] != 0), len(m["o"]))
        for name, w in (("zv2h", 120), ("zv6h", 360)):
            a = max(t - w, fv)
            nn = t - a
            exp = (sum(1 for k in range(a, t) if m["qv"][k] == 0) / nn) if nn >= 30 else float("nan")
            got = getattr(r, name)
            if not ((np.isnan(exp) and np.isnan(got)) or abs(exp - got) < 1e-12):
                bad.append({"ep": r.ep_id, name: (got, exp)})
    json.dump({"episodes": n, "mismatch": bad}, open(out, "w"), ensure_ascii=False, indent=1)
    print({"episodes": n, "mismatch": len(bad)})
    return 1 if bad else 0


def gate_label(table, out):
    e = pd.read_parquet(table)
    ok_eq = bool(((e["o_x1_t60_f01"] == 3).astype(int) == e["y"]).all())
    by = e.groupby("side")["y"].sum().to_dict()
    byh = e.groupby(["side", "half"])["y"].agg(["size", "sum"]).reset_index().to_dict("records")
    res = {"y_equals_o3_depth": ok_eq, "breakouts_by_side_U0_indep_all_days": {k: int(v) for k, v in by.items()}, "expected_TK590": {"long": 1297, "short": 1582},
           "by_half": byh, "n": int(len(e))}
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1, default=int)
    print(res)
    return 0 if ok_eq and by.get("long") == 1297 and by.get("short") == 1582 else 1


def gate_cells(table, pairs_dir, out, n=6):
    """Клетки шорт-листа пересчитаны «в лоб»: границы из замороженных корзин (по значению), n, k, M, E1 — прямым счётом по маске."""
    df = T.prepare(pd.read_parquet(table))
    rows, bad = [], []
    for fn in sorted(f for f in os.listdir(pairs_dir) if f.startswith("frozen_") and f.endswith(".pkl")):
        fz = pickle.load(open(f"{pairs_dir}/{fn}", "rb"))
        side, sh = fz["side"], fz["search"]
        S = df[(df.side == side) & (df.half == sh)].reset_index(drop=True)
        add = fz["add"]
        r = S["d"].to_numpy() - add.predict(S)
        for c in fz["shortlist"][:n]:
            sc = c["scheme"]
            b1, b2 = fz["bk"][(c["f1"], sc)], fz["bk"][(c["f2"], sc)]

            def code(b, x):
                # «в лоб»: бакет по явным границам и таблице ранг-кодов
                e_ = b.edges
                c_ = sum(1 for e0 in e_ if x > e0)
                return int(b.lut[c_]) if not np.isnan(x) else -1

            mask = np.array([code(b1, x) == c["b1"] and code(b2, y) == c["b2"] for x, y in zip(S[c["f1"]], S[c["f2"]])])
            rec = {"file": fn, "id": c["id"], "n": (int(mask.sum()), c["n"]), "k": (int(S["y"].to_numpy()[mask].sum()), c["k"]),
                   "M": (float(r[mask].mean()), c["M"]), "E1": (float(S["d"].to_numpy()[mask].mean()), c["E1"])}
            rows.append(rec)
            if rec["n"][0] != rec["n"][1] or rec["k"][0] != rec["k"][1] or abs(rec["M"][0] - rec["M"][1]) > 1e-9 or abs(rec["E1"][0] - rec["E1"][1]) > 1e-9:
                bad.append(rec)
    json.dump({"cells": len(rows), "mismatch": bad}, open(out, "w"), ensure_ascii=False, indent=1)
    print({"cells": len(rows), "mismatch": len(bad)})
    return 1 if bad or len(rows) < 5 else 0


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "ctrl":
        sys.exit(gate_ctrl(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 40))
    if mode == "zv":
        sys.exit(gate_zv(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 250))
    if mode == "label":
        sys.exit(gate_label(sys.argv[2], sys.argv[3]))
    if mode == "cells":
        sys.exit(gate_cells(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 6))
