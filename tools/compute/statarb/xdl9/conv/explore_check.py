#!/usr/bin/env python3
"""TK-493, П-39, G-X-7: НЕЗАВИСИМЫЙ пересчёт таблицы по сделкам (stdlib: csv, gzip, math; без numpy и без общих функций с раннером и explore_stats).

Читает out/<tag>/trades/<id>.csv.gz и out/<tag>/den/<id>.csv, пересчитывает для выбранных вариантов: сделки, активы, gross/комиссию/фандинг/net на сделку, c*, net при 8,82 и 5,04, ряд y_d
(закрытые + MTM в последние сутки, знаменатель из den), среднее, нижнюю границу блочного t (t-квантиль - численным интегрированием плотности), net и y_d по половинам;
сравнивает со столбцами tables/variants_main.csv. Допуск: 1e-9 относительный, но не жёстче 1e-7 абсолютного (сделки в csv.gz с 9 значащими цифрами); нижние границы интервала - 1e-6.
  python explore_check.py --out ~/xdl9/c2x --tag main --ids REF-B1,REF-B2,A-B1-Linf-T1440,B-B1-hT-phi0,E-B1-no-bybit
"""
import argparse
import csv
import gzip
import math
import os

D0, D_LAST, P0, H1_LAST = 2465, 2499, 2466, 2478


def read_trades(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        first = f.readline()
        assert first.startswith("# ПОИСК"), "нет баннера"
        rd = csv.DictReader(f)
        return list(rd)


def read_den(path):
    with open(path, encoding="utf-8", newline="") as f:
        f.readline()
        return {int(r["day"]): int(r["den"]) for r in csv.DictReader(f)}


def t_cdf(x, df):
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    n = 40000
    h = abs(x) / n
    s = 0.0
    for i in range(n + 1):
        u = i * h
        w = 1 if i in (0, n) else (4 if i % 2 else 2)
        s += w * (1 + u * u / df) ** (-(df + 1) / 2)
    area = c * s * h / 3
    return 0.5 + (area if x >= 0 else -area)


def t_ppf(p, df):
    lo, hi = 0.0, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def block_len(n):
    return max(1, math.ceil(round(n ** (1 / 3), 12)))


def block_lo(series, alpha=0.10):
    n = len(series)
    b = block_len(n)
    nb = math.ceil(n / b)
    if nb < 3:
        return None
    mean = sum(series) / n
    ss = 0.0
    for k in range(nb):
        seg = series[k * b:(k + 1) * b]
        r = sum(seg) - mean * len(seg)
        ss += r * r
    se = math.sqrt(nb / (nb - 1) * ss / (n * n))
    return mean - t_ppf(1 - alpha / 2, nb - 1) * se


def recompute(rows, den):
    n = len(rows)
    out = {"n": n}
    if not n:
        return out
    g = sum(float(r["gross"]) for r in rows) / n
    fe = sum(float(r["fee"]) for r in rows) / n
    fu = sum(float(r["fund"]) for r in rows) / n
    ne = sum(float(r["net"]) for r in rows) / n
    out.update(gross=g, fee=fe, fund=fu, net=ne, cstar=g + fu, net_882=g + fu - 8.82, net_504=g + fu - 5.04, assets=len({r["key"] for r in rows}))
    day_net, mtm, n_open = {}, 0.0, 0
    for r in rows:
        if int(r["mtm"]) == 1:
            mtm += float(r["net"])
            n_open += 1
        else:
            d = D0 + int(r["f_out"]) // 1440
            day_net[d] = day_net.get(d, 0.0) + float(r["net"])
    days = [d for d in range(P0, D_LAST + 1) if den.get(d, 0) > 0]
    ser = {d: day_net.get(d, 0.0) / den[d] for d in days}
    if mtm != 0.0 or n_open > 0:
        if den.get(D_LAST, 0) > 0:
            ser[D_LAST] = ser.get(D_LAST, 0.0) + mtm / den[D_LAST]
        else:
            ser[D_LAST] = mtm / n_open
    ds = sorted(ser)
    vals = [ser[d] for d in ds]
    out["yd"] = sum(vals) / len(vals)
    out["yd_lo_t"] = block_lo(vals)
    for nm, sel in (("h1", [d for d in ds if d <= H1_LAST]), ("h2", [d for d in ds if d > H1_LAST])):
        v = [ser[d] for d in sel]
        out[nm + "_yd"] = sum(v) / len(v) if v else None
        out[nm + "_yd_lo_t"] = block_lo(v) if v else None
    for nm, cond in (("h1", lambda r: (D_LAST if int(r["mtm"]) else D0 + int(r["f_out"]) // 1440) <= H1_LAST), ("h2", lambda r: (D_LAST if int(r["mtm"]) else D0 + int(r["f_out"]) // 1440) > H1_LAST)):
        sub = [float(r["net"]) for r in rows if cond(r)]
        out[nm + "_n"] = len(sub)
        out[nm + "_net"] = sum(sub) / len(sub) if sub else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/xdl9/c2x"))
    ap.add_argument("--tag", default="main")
    ap.add_argument("--ids", required=True)
    a = ap.parse_args(argv)
    with open(os.path.join(a.out, "tables", "variants_main.csv"), encoding="utf-8", newline="") as f:
        f.readline()
        table = {r["id"]: r for r in csv.DictReader(f)}
    bad = 0
    for vid in a.ids.split(","):
        rows = read_trades(os.path.join(a.out, "out", a.tag, "trades", vid + ".csv.gz"))
        den = read_den(os.path.join(a.out, "out", a.tag, "den", vid + ".csv"))
        mine = recompute(rows, den)
        t = table[vid]
        for k, v in mine.items():
            if v is None or t.get(k, "") == "":
                continue
            want = float(t[k])
            tol = 1e-6 if k.endswith("_lo_t") else 1e-9
            # сделки в csv.gz записаны с 9 значащими цифрами (формат окна): среднее по ним отличается от расчёта в памяти на ~1e-8 bps - допуск 1e-7 абсолютный
            if abs(v - want) > max(tol * abs(want), 1e-7 if not k.endswith("_lo_t") else 1e-6):
                bad += 1
                print("РАСХОЖДЕНИЕ %s %s: независимо %.12g, таблица %.12g" % (vid, k, v, want))
        print("%-26s сверено %d столбцов" % (vid, sum(1 for k, v in mine.items() if v is not None and t.get(k, "") != "")))
    print("G-X-7: расхождений %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
