#!/usr/bin/env python3
"""TK-493 w5 F (П-37 r3.2 D5 iv-v, аудит L1-01): ТОЧНЫЙ двухногий пересчёт фандинга сделок D2 по свечам и сырым ставкам - чувствительность вне семьи ПОСЛЕ счёта.

Зачем. Ядро считает фандинг D2 по разностному ряду d = r_Y − r_X на метках выплат ноги Y (d2_diff_funding.py): каждая выплата X «привязана» к первой метке Y
не раньше неё, начисление идёт на цене Y в минуту метки. Точная модель (та же, что у ядра для двух ног со своими ставками): нога держится строго через минуту
выплаты (вход — минута fill_in, выход — fill_out; выплата в минуту входа и в минуту выхода не начисляется — как в `kernel.rs`), и на каждой выплате своей ноги

    funding_bps = 1e4 · Σ_{m ∈ (fill_in, fill_out)} [ − q_Y · open_Y(m) · r_Y(m)  −  q_X · open_X(m) · r_X(m) ],      q_Y = dir / open_Y(fill_in),   q_X = −dir / open_X(fill_in)

(β = 1, вес X = 1, D2 без задержки ноги X; open(m) — последний конечный open не позже минуты m, как `price_at`). Ряд d даёт ту же сумму при равных сетках и равных ценах
ног; на неравных сетках момент начисления X сдвинут к метке Y, а цена X заменена ценой Y — отсюда расхождение, которое здесь измеряется на сделку.

  python d2_exact_funding.py run --run out --panels ~/xdl9/trade --funding ~/xdl9/funding --out agg [--stages w1] [--d0 2479 --d1 2500]
      читает `out/<стадия>/D2_*.trades.csv` (имя файла `<кл>__<биржа Y>__<биржа X>[__shardN]`), панели и СЫРЫЕ ставки бирж (не ряды d), пишет
      `d2_exact_trades.csv` (по сделке: funding ядра, точный, разность) и `d2_exact_cells.csv` (по клетке: Σ и среднее разности, доля сделок с |разность| > 1 bps,
      99-й процентиль |разности|, net клетки с точным фандингом против ядра). Статуса нет, в `M` не входит. Читает окно - только после F8.
  python d2_exact_funding.py selftest

G7 для ряда d (§11.7): к сверке «руками по csv.gz» добавлен этот пересчёт - сумма Σ(funding ядра − точный) по клетке должна быть мала в сравнении с комиссией круга 12,6 bps
на сделку; печатается в d2_exact_cells.csv, пороги не назначаются.
"""
import csv, glob, gzip, math, os, sys

import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_DAY = 1440


def read_open(path):
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first_min = int(np.frombuffer(h[8:16], dtype="<i8")[0])
    n = int(np.frombuffer(h[16:24], dtype="<u8")[0])
    return first_min, np.fromfile(path, dtype="<f8", count=n, offset=24)


def read_rates(path):
    """Ставки `time_ms,rate,interval_h` -> {минута: Σ ставок} (две строки в одну минуту складываются, как `rates_at`)."""
    out = {}
    if not os.path.exists(path):
        return out
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd, None)
        for r in rd:
            if len(r) >= 2:
                m = (int(r[0]) - GRID0_MS) // 60_000
                out[m] = out.get(m, 0.0) + float(r[1])
    return out


def price_at(first_min, op, m):
    """Последний конечный open не позже абсолютной минуты m (как kernel::price_at)."""
    i = m - first_min
    while i >= 0:
        if math.isfinite(op[i]):
            return float(op[i])
        i -= 1
    return float("nan")


def exact_trade(d, fill_in, fill_out, py, px, ry, rx):
    """Точный двухногий фандинг сделки D2 в bps (формула в docstring). py, px - (first_min, open[]), ry, rx - {минута: ставка}."""
    fy, oy = py
    fx, ox = px
    qy = d / price_at(fy, oy, fill_in)
    qx = -d / price_at(fx, ox, fill_in)
    acc = 0.0
    for m, r in ry.items():
        if fill_in < m < fill_out and r != 0.0:
            acc -= qy * price_at(fy, oy, m) * r
    for m, r in rx.items():
        if fill_in < m < fill_out and r != 0.0:
            acc -= qx * price_at(fx, ox, m) * r
    return acc * 1e4


def exch_of(path):
    parts = os.path.basename(path).split(".")[0].split("__")
    return parts[1], parts[2]


def cmd_run(a):
    stages = a["stages"].split(",")
    cache_p, cache_f = {}, {}

    def panel(ex, sym):
        k = (ex, sym)
        if k not in cache_p:
            fp = os.path.join(a["panels"], ex, sym + ".sap")
            cache_p[k] = read_open(fp) if os.path.exists(fp) else None
        return cache_p[k]

    def rates(ex, sym):
        k = (ex, sym)
        if k not in cache_f:
            cache_f[k] = read_rates(os.path.join(a["funding"], ex, sym + ".csv.gz"))
        return cache_f[k]
    rows, per_cell = [], {}
    skipped = 0
    for st in stages:
        for fp in sorted(glob.glob(os.path.join(a["run"], st, "D2_*.trades.csv"))):
            exy, exx = exch_of(fp)
            with open(fp, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    py, px = panel(exy, r["y"]), panel(exx, r["x"])
                    if py is None or px is None:
                        skipped += 1
                        continue
                    fi, fo = int(r["fill_in"]), int(r["fill_out"])
                    fe = exact_trade(int(r["dir"]), fi, fo, py, px, rates(exy, r["y"]), rates(exx, r["x"]))
                    fk = float(r["funding_bps"])
                    net = float(r["net_bps"])
                    rows.append([r["cell"], r["y"], r["x"], r["dir"], fi, fo, fk, fe, fe - fk, net, net - fk + fe])
                    c = per_cell.setdefault(r["cell"], [])
                    c.append((fk, fe, net))
    os.makedirs(a["out"], exist_ok=True)
    with open(os.path.join(a["out"], "d2_exact_trades.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["cell", "y", "x", "dir", "fill_in", "fill_out", "funding_kernel_bps", "funding_exact_bps", "diff_bps", "net_kernel_bps", "net_exact_bps"])
        w.writerows(rows)
    with open(os.path.join(a["out"], "d2_exact_cells.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["cell", "trades", "sum_diff_bps", "mean_diff_bps", "share_abs_diff_gt_1bps", "p99_abs_diff_bps", "mean_net_kernel", "mean_net_exact"])
        for c, v in sorted(per_cell.items()):
            diffs = [abs(e - k) for k, e, _ in v]
            diffs.sort()
            p99 = diffs[min(len(diffs) - 1, int(math.ceil(0.99 * len(diffs))) - 1)]
            n = len(v)
            w.writerow([c, n, sum(e - k for k, e, _ in v), sum(e - k for k, e, _ in v) / n, sum(1 for d in diffs if d > 1.0) / n, p99,
                        sum(x for _, _, x in v) / n, sum(x - k + e for k, e, x in v) / n])
    print("d2_exact_funding: сделок %d, клеток %d, пропущено (нет панели) %d -> %s" % (len(rows), len(per_cell), skipped, a["out"]))


def selftest():
    # Y: open 100 на всех минутах 0..20; X: open 50. Вход на минуте 2, выход на минуте 12, dir = +1 (лонг Y, шорт X).
    n = 21
    py = (0, np.full(n, 100.0))
    px = (0, np.full(n, 50.0))
    ry = {2: 0.0010, 5: 0.0004, 12: 0.0020}          # выплаты в минуту входа (2) и выхода (12) не начисляются
    rx = {3: 0.0001, 5: 0.0003, 11: 0.0002}
    got = exact_trade(1, 2, 12, py, px, ry, rx)
    qy, qx = 1 / 100.0, -1 / 50.0
    want = (-(qy * 100.0 * 0.0004) - qx * 50.0 * (0.0001 + 0.0003 + 0.0002)) * 1e4
    assert abs(got - want) < 1e-9, (got, want)
    # Равные сетки и равные цены ног: точный фандинг = −dir·(r_Y − r_X) в bps номинала (ряд d ядра): q_Y·price = dir, q_X·price = −dir
    ry2, rx2 = {5: 0.0004, 8: 0.0001}, {5: 0.0003, 8: 0.0001}
    g2 = exact_trade(-1, 2, 12, py, px, ry2, rx2)
    expect = (+1.0 * (0.0004 - 0.0003) + 1.0 * (0.0001 - 0.0001)) * 1e4          # dir = −1: −q_Y·price·r_Y = +r_Y, −q_X·price·r_X = −r_X
    assert abs(g2 - expect) < 1e-9, (g2, expect)
    # цена в минуту выплаты - последний конечный open (дыра)
    py3 = (0, np.array([100.0, 100.0, 100.0, np.nan, 110.0] + [110.0] * 16))
    g3 = exact_trade(1, 2, 12, py3, px, {3: 0.001}, {})
    assert abs(g3 - (-(1 / 100.0) * 100.0 * 0.001 * 1e4)) < 1e-9
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--run", required=True)
        ap.add_argument("--panels", required=True)
        ap.add_argument("--funding", required=True)
        ap.add_argument("--out", required=True)
        ap.add_argument("--stages", default="w1")
        cmd_run(vars(ap.parse_args()))
    else:
        raise SystemExit(__doc__)
