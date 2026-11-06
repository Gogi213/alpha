#!/usr/bin/env python3
"""TK-493 F3 шаг 2 (G7/G8): пересчёт сделок `statarb run` руками из панелей `.sap` и ставок `.csv.gz`, без ядра.

Проверяется то, что не видно по суммам: что ставки фандинга попали в минуты панелей (эпоха E-010), что выплаты
начисляются только за время держания, что комиссия, валовый результат и итог сходятся с csv сделок.
Допущения те же, что в ядре и в тестах: β = 1 (cross, либо pair с --beta-fixed 1.0), обе ноги — перпы (taker 3,15 bps
за сторону), нога Y лонг при dir=+1; количество Y = dir/open_Y(fill_in), X = −dir/open_X(fill_x). Фандинг: за каждую
минуту t строго между входом ноги и исполнением выхода, где есть выплата, нога платит −qty·price·rate; price — open
минуты, а если там нет сделок — последний известный open.

  check_run_ext.py --trades T.csv --panels-y DIR --panels-x DIR [--funding-y DIR] [--funding-x DIR] [--n 5]
  check_run_ext.py --open O.csv --end-minute M --panels-y DIR --panels-x DIR [--funding-y DIR] [--funding-x DIR]

Второй вид — позиции, открытые на конце окна (`--open-out` при `--mtm-open`): оценка по последнему валидному close
каждой ноги не позже `--end-minute` (минута сетки: последняя минута окна задания = min(to_day·1440 − 1, конец рядов));
выплаты начисляются за минуты после входа ноги до конца окна включительно.

Только стандартная библиотека (на Deck нет numpy). Код возврата 0 — все выбранные сделки сошлись (допуск 1e-6 bps).
"""
import argparse
import csv
import gzip
import os
import struct
import sys

G0_MS = 1_577_836_800_000
MIN_MS = 60_000
FEE_TAKER = 3.15  # bps за сторону, перп после возврата 10 % (lob::costs, В-47/В-88)


def read_sap(path):
    b = open(path, "rb").read()
    if b[:4] != b"SAP1":
        raise SystemExit(f"не панель SAP1: {path}")
    first, n = struct.unpack("<qQ", b[8:24])
    op = struct.unpack(f"<{n}d", b[24:24 + 8 * n])
    cl = struct.unpack(f"<{n}d", b[24 + 8 * n:24 + 16 * n])
    return first, op, cl


def read_funding(dirpath, name):
    """Выплаты по минутам сетки панелей: {минута: сумма ставок}; нет файла — пусто."""
    if dirpath is None:
        return {}
    p = os.path.join(dirpath, f"{name}.csv.gz")
    if not os.path.exists(p):
        return {}
    ev = {}
    for line in gzip.open(p, "rt").read().splitlines()[1:]:
        f = line.split(",")
        if len(f) < 2 or not f[0]:
            continue
        m = (int(f[0]) - G0_MS) // MIN_MS
        ev[m] = ev.get(m, 0.0) + float(f[1])
    return ev


def open_at(first, op, minute):
    """open минуты, а если там нет сделок — последний известный open раньше (price_at ядра)."""
    i = minute - first
    while i >= 0:
        if op[i] == op[i]:  # не NaN
            return op[i]
        i -= 1
    raise SystemExit(f"нет open до минуты {minute}")


def leg_fund(first, op, ev, qty, t_from, t_to):
    """Начисление ноги за минуты t_from < t < t_to."""
    s = 0.0
    for m, r in ev.items():
        if t_from < m < t_to:
            s -= qty * open_at(first, op, m) * r
    return s


def recompute(row, py, px, fy, fx):
    d = int(row["dir"])
    fin, fout = int(row["fill_in"]), int(row["fill_out"])
    fxm = int(row["fill_x"]) if "fill_x" in row else fin
    (f0y, oy, _), (f0x, ox, _) = py, px
    pin_y, pin_x = oy[fin - f0y], ox[fxm - f0x]
    qy, qx = d / pin_y, -d / pin_x
    pout_y, pout_x = oy[fout - f0y], ox[fout - f0x]
    gross = 1e4 * (qy * (pout_y - pin_y) + qx * (pout_x - pin_x))
    fee = FEE_TAKER * (abs(qy) * (pin_y + pout_y) + abs(qx) * (pin_x + pout_x))
    fund = 1e4 * (leg_fund(f0y, oy, fy, qy, fin, fout) + leg_fund(f0x, ox, fx, qx, fxm, fout))
    return gross, fee, fund, gross - fee + fund


def last_valid_close(first, cl, lo, hi):
    """(минута, close) последней свечи с ценой на минутах [lo, hi]; нет — (lo, None)."""
    for m in range(hi, lo - 1, -1):
        i = m - first
        if 0 <= i < len(cl) and cl[i] == cl[i]:
            return m, cl[i]
    return lo, None


def recompute_open(row, py, px, fy, fx, end):
    d = int(row["dir"])
    fin = int(row["fill_in"])
    fxm = int(row["fill_x"]) if row["fill_x"] != "" else None
    (f0y, oy, cy), (f0x, ox, cx) = py, px
    end = min(end, f0y + len(oy) - 1, f0x + len(ox) - 1)
    pin_y = oy[fin - f0y]
    qy = d / pin_y
    my, cly = last_valid_close(f0y, cy, fin, end)
    cly = pin_y if cly is None else cly
    gross = qy * (cly - pin_y)
    fee = FEE_TAKER * abs(qy) * (pin_y + cly)
    fund = leg_fund(f0y, oy, fy, qy, fin, end + 1)
    oldest = my
    if fxm is not None:
        pin_x = ox[fxm - f0x]
        qx = -d / pin_x
        mx, clx = last_valid_close(f0x, cx, fxm, end)
        clx = pin_x if clx is None else clx
        gross += qx * (clx - pin_x)
        fee += FEE_TAKER * abs(qx) * (pin_x + clx)
        fund += leg_fund(f0x, ox, fx, qx, fxm, end + 1)
        oldest = min(oldest, mx)
    gross, fund = 1e4 * gross, 1e4 * fund
    return gross, fee, fund, gross - fee + fund, end - oldest


def early_entries(rows, min_fill_in):
    """Сколько строк csv с fill_in раньше `min_fill_in` (None — проверка выключена, 0)."""
    if min_fill_in is None:
        return 0
    return sum(1 for r in rows if int(r["fill_in"]) < min_fill_in)


def main_open(a):
    rows = list(csv.DictReader(open(a.open, encoding="utf-8")))
    if not rows:
        raise SystemExit("в csv нет открытых позиций")
    bad = early_entries(rows, a.min_fill_in)
    for r in rows[: a.n]:
        py = read_sap(os.path.join(a.panels_y, f"{r['y']}.sap"))
        px = read_sap(os.path.join(a.panels_x, f"{r['x']}.sap"))
        g, f, fu, n, stale = recompute_open(
            r, py, px, read_funding(a.funding_y, r["y"]), read_funding(a.funding_x, r["x"]), a.end_minute)
        want = {k: float(r[k]) for k in ("gross_bps", "fee_bps", "funding_bps", "net_bps")}
        got = {"gross_bps": g, "fee_bps": f, "funding_bps": fu, "net_bps": n}
        diff = max(abs(want[k] - got[k]) for k in want)
        ok = diff <= a.tol and stale == int(r["stale_min"])
        bad += not ok
        print(f"{'OK ' if ok else 'ОШИБКА'} job={r['job']} {r['y']}/{r['x']} dir={r['dir']} fill_in={r['fill_in']} "
              f"fill_x={r['fill_x'] or '-'}: net csv {want['net_bps']:.6f} руками {n:.6f}; funding csv {want['funding_bps']:.6f} "
              f"руками {fu:.6f}; stale csv {r['stale_min']} руками {stale}; max|Δ| {diff:.2e}")
    print(f"проверено {min(len(rows), a.n)} открытых позиций, расхождений (в том числе входов раньше границы) {bad}")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades")
    ap.add_argument("--open", help="csv --open-out (оценка открытых позиций)")
    ap.add_argument("--end-minute", type=int, help="последняя минута окна (для --open)")
    ap.add_argument("--panels-y", required=True)
    ap.add_argument("--panels-x", required=True)
    ap.add_argument("--funding-y")
    ap.add_argument("--funding-x")
    ap.add_argument("--n", type=int, default=5, help="сколько сделок с ненулевым фандингом проверить (плюс столько же без)")
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--min-fill-in", type=int,
                    help="TK-493 w5 (D5(ii)): run запущен с --no-entry-before-from — у ВСЕХ строк csv fill_in >= этой минуты "
                         "(from_day·1440 задания); иначе код возврата 1")
    a = ap.parse_args()
    if a.open:
        if a.end_minute is None:
            raise SystemExit("--open требует --end-minute")
        return main_open(a)
    if not a.trades:
        raise SystemExit("нужен --trades или --open")
    rows = list(csv.DictReader(open(a.trades, encoding="utf-8")))
    if not rows:
        raise SystemExit("в csv нет сделок")
    early = early_entries(rows, a.min_fill_in)
    nz = [r for r in rows if float(r["funding_bps"]) != 0.0][: a.n]
    zr = [r for r in rows if float(r["funding_bps"]) == 0.0][: a.n]
    pick = nz + zr
    cache = {}

    def panel(d, name):
        k = (d, name)
        if k not in cache:
            cache[k] = read_sap(os.path.join(d, f"{name}.sap"))
        return cache[k]

    bad = 0
    for r in pick:
        if "+" in r["x"]:
            print("корзина (x со знаком +) не поддержана этим скриптом:", r["x"])
            continue
        py, px = panel(a.panels_y, r["y"]), panel(a.panels_x, r["x"])
        g, f, fu, n = recompute(r, py, px, read_funding(a.funding_y, r["y"]), read_funding(a.funding_x, r["x"]))
        want = {k: float(r[k]) for k in ("gross_bps", "fee_bps", "funding_bps", "net_bps")}
        got = {"gross_bps": g, "fee_bps": f, "funding_bps": fu, "net_bps": n}
        diff = max(abs(want[k] - got[k]) for k in want)
        ok = diff <= a.tol
        bad += not ok
        print(f"{'OK ' if ok else 'ОШИБКА'} job={r['job']} {r['y']}/{r['x']} dir={r['dir']} fill_in={r['fill_in']}"
              f"{' fill_x=' + r['fill_x'] if 'fill_x' in r else ''} fill_out={r['fill_out']}: "
              f"funding csv {want['funding_bps']:.6f} руками {fu:.6f}; net csv {want['net_bps']:.6f} руками {n:.6f}; max|Δ| {diff:.2e}")
    print(f"проверено {len(pick)} сделок (с фандингом {len(nz)}), расхождений {bad}"
          + (f"; входов раньше минуты {a.min_fill_in}: {early} из {len(rows)}" if a.min_fill_in is not None else ""))
    sys.exit(1 if bad or not nz or early else 0)


if __name__ == "__main__":
    main()
