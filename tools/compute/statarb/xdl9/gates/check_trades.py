#!/usr/bin/env python3
"""TK-493 F6 (G7, G8, G8b): независимый пересчёт `statarb run` по СЫРЬЮ (`candles/<SYM>.csv.gz`, `funding/<SYM>.csv.gz`),
без `.sap` и без ядра. Полная повторная реализация одного задания (нога Y против ноги X): окно z/β, сигнал, исполнение
по open следующей минуты, выход, комиссия, фандинг, остаток открытой позиции и её оценка по последнему close. Результат
сверяется с выходами `run`: `--trades-out` (все сделки задания), `--cells-out` (счётчики), `--open-out` (открытая позиция).

Правила чтения сырья — те же, что в `convert` (panel.rs): минута валидна, если строка есть и объём (6-е поле) не равен
0.0; первая и последняя валидные минуты ряда — границы спана ноги; общий спан задания = пересечение спанов ног, окно
задания [lo, hi]: lo = max(начало общего спана, from_day·1440 − window), hi = min(конец общего спана, to_day·1440 − 1).
Минута выплаты фандинга = floor((time_ms − 2020-01-01T00:00Z) / 60000) (E-010). Только стандартная библиотека
(на Deck нет numpy).

Модель (kernel.rs, без задержки ноги X):
  * z/β на закрытии минуты t по окну последних `window` минут сетки (только минуты, где обе ноги валидны, не меньше
    `min_obs`): β — МНК ln(close_Y) на ln(close_X) либо фикс.; z — остаток точки t от среднего остатка окна, делённый
    на sqrt(ssr/n);
  * правило z: вход при z ≤ −entry (long Y) либо z ≥ +entry (short Y); выход при dir·z ≥ −exit_z, либо dir·z ≤ −stop_z,
    либо через max_hold минут; правило funding (D3): трейлинг-среднее k последних выплат ноги Y (минута ≤ t) × hold
    выплат в bps против круга издержек 2·3,15·(1 + |β|);
  * исполнение — open минуты t+1, если он есть у обеих ног, иначе вход/выход ждёт (entry_deferred/exit_deferred);
  * количества: Y = dir/open_Y, X = −dir·β/open_X (β заморожена на сигнале); комиссия 3,15 bps на сторону на ногу;
  * фандинг: за каждую минуту строго между входом и исполнением выхода, где есть выплата, нога платит
    −qty·price·rate, price — open минуты либо последний известный open.

Запуск (пример):
  check_trades.py --root ~/xdl/out --y-ex okx --y BTC-USDT-SWAP --x-ex okx --x BTC-USDT-SWAP ... (см. --help)
Код возврата 0 — все сделки задания и счётчики сошлись.
"""
import argparse
import csv
import gzip
import math
import os
import sys
from math import fsum, isfinite, log, sqrt

G0_MS = 1_577_836_800_000
MIN_MS = 60_000
DAY = 1440
FEE = 3.15  # bps на сторону на ногу, перп-тейкер после возврата 10 % (lob::costs, В-47/В-88)
EPS = sys.float_info.epsilon
NAN = float("nan")


class Leg:
    """Сырьё одного контракта: валидные минуты (open, close) и выплаты фандинга [(минута, ставка)] в порядке файла."""

    def __init__(self, root, ex, sym, funding_dir=True):
        base = os.path.join(root, ex)
        self.open, self.close = {}, {}
        with gzip.open(os.path.join(base, "candles", sym + ".csv.gz"), "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                fs = line.rstrip("\r\n").split(",")
                if len(fs) < 5 or not fs[0].strip():
                    continue
                try:
                    t = int(fs[0])
                except ValueError:
                    if i == 0:
                        continue
                    raise
                rel = t - G0_MS
                if rel % MIN_MS:
                    raise SystemExit(f"{ex}/{sym}: время {t} не на минуте")
                v = None
                if len(fs) > 5:
                    try:
                        v = float(fs[5])
                    except ValueError:
                        v = None
                if v == 0.0:
                    continue
                m = rel // MIN_MS
                self.open[m] = float(fs[1])
                self.close[m] = float(fs[4])
        if not self.open:
            raise SystemExit(f"{ex}/{sym}: нет валидных минут")
        self.first, self.last = min(self.open), max(self.open)
        self.fund = []
        p = os.path.join(base, "funding", sym + ".csv.gz")
        if funding_dir and os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    fs = line.rstrip("\r\n").split(",")
                    if len(fs) < 2 or not fs[0]:
                        continue
                    self.fund.append(((int(fs[0]) - G0_MS) // MIN_MS, float(fs[1])))


def by_minute(ev):
    d = {}
    for m, r in ev:
        d[m] = d.get(m, 0.0) + r
    return d


def price_at(op, i):
    """open минуты i, а если там нет сделок — последний известный open раньше (price_at ядра)."""
    while not isfinite(op[i]):
        i -= 1
    return op[i]


def simulate(P, Y, X, lo, hi, use_fund):
    """Одно задание: возвращает (сделки, открытая позиция или None, счётчики). Минуты в сделках абсолютные."""
    n = hi - lo + 1
    g = lambda d, i: d.get(lo + i, NAN)  # noqa: E731
    oy = [g(Y.open, i) for i in range(n)]
    ox = [g(X.open, i) for i in range(n)]
    cy = [g(Y.close, i) for i in range(n)]
    cx = [g(X.close, i) for i in range(n)]
    ly = [log(c) if c == c else NAN for c in cy]
    lx = [log(c) if c == c else NAN for c in cx]
    W, min_obs = P["window"], P["min_obs"]
    fixed = P.get("beta_fixed")
    rule = P["rule"]
    fk, fh = P.get("funding_k", 0), P.get("hold_payments", 0)
    fy = by_minute(Y.fund) if use_fund else {}
    fx = by_minute(X.fund) if use_fund else {}
    ev_y = Y.fund if use_fund else []
    cost = lambda b: 2 * FEE + abs(b) * 2 * FEE  # noqa: E731  (wsum = 1)

    def zb(i):
        if not (isfinite(ly[i]) and isfinite(lx[i])):
            return NAN, NAN
        a = max(0, i - W + 1)
        xs, ys = [], []
        for yy, xx in zip(ly[a:i + 1], lx[a:i + 1]):
            if yy == yy and xx == xx:
                xs.append(xx)
                ys.append(yy)
        k = len(xs)
        if k < max(min_obs, 2):
            return NAN, NAN
        mx, my = fsum(xs) / k, fsum(ys) / k
        dx = [v - mx for v in xs]
        dy = [v - my for v in ys]
        cxx = fsum(v * v for v in dx)
        cxy = fsum(a_ * b_ for a_, b_ in zip(dx, dy))
        cyy = fsum(v * v for v in dy)
        if fixed is None:
            if cxx <= 0.0:
                return NAN, NAN
            beta = cxy / cxx
        else:
            beta = fixed
        guard = cyy - 2.0 * beta * cxy + beta * beta * cxx
        scale = cyy + beta * beta * cxx
        if not guard > 8.0 * EPS * scale:
            return beta, NAN
        ssr = fsum((b_ - beta * a_) ** 2 for a_, b_ in zip(dx, dy))
        return beta, (dy[-1] - beta * dx[-1]) / sqrt(ssr / k)

    cnt = dict(obs_minutes=0, gap_minutes=0, entries=0, entry_deferred=0, exit_deferred=0, trades=0, minutes_in_pos=0)
    trades = []
    d = 0
    pend = None
    sig_in = fill_in = 0
    qy = qx = pin_y = pin_x = fund = x_beta = 0.0
    ring, ring_len, ring_head, cur_k = [0.0] * fk, 0, 0, 0
    r_mean = NAN
    for i in range(n):
        ft = lo + i
        ok = isfinite(oy[i]) and isfinite(ox[i])
        filled = False
        if pend is not None:
            if pend[0] == "E":
                _, dd, beta, sg = pend
                pend = None
                if ok:
                    d, sig_in, fill_in, x_beta = dd, sg, i, beta
                    qy, pin_y = dd / oy[i], oy[i]
                    qx, pin_x = -dd * beta / ox[i], ox[i]
                    fund = 0.0
                    cnt["entries"] += 1
                    filled = True
                else:
                    cnt["entry_deferred"] += 1
            else:
                sg = pend[1]
                if ok:
                    gross = qy * (oy[i] - pin_y) + qx * (ox[i] - pin_x)
                    fee = abs(qy) * (pin_y * FEE + oy[i] * FEE) + abs(qx) * (pin_x * FEE + ox[i] * FEE)
                    gb, fb = gross * 1e4, fund * 1e4
                    trades.append(dict(dir=d, sig_in=lo + sig_in, fill_in=lo + fill_in, sig_out=lo + sg, fill_out=ft,
                                       gross_bps=gb, fee_bps=fee, funding_bps=fb, net_bps=gb - fee + fb,
                                       qy=qy, qx=qx, beta=x_beta, pin_y=pin_y, pin_x=pin_x, pout_y=oy[i], pout_x=ox[i]))
                    cnt["trades"] += 1
                    d = 0
                    pend = None
                else:
                    cnt["exit_deferred"] += 1
        hold = d != 0 and not filled
        ry, rx = fy.get(ft, 0.0), fx.get(ft, 0.0)
        if hold and ry != 0.0:
            fund -= qy * price_at(oy, i) * ry
        if hold and rx != 0.0:
            fund -= qx * price_at(ox, i) * rx
        if d != 0:
            cnt["minutes_in_pos"] += 1
        obs = isfinite(ly[i]) and isfinite(lx[i])
        cnt["obs_minutes" if obs else "gap_minutes"] += 1
        if rule == "z" or fixed is None:
            beta, z = zb(i) if obs else (NAN, NAN)
        else:
            beta, z = fixed, NAN
        if rule == "funding":
            got = False
            while cur_k < len(ev_y) and ev_y[cur_k][0] <= ft:
                if ring_len < fk:
                    ring[ring_len] = ev_y[cur_k][1]
                    ring_len += 1
                else:
                    ring[ring_head] = ev_y[cur_k][1]
                    ring_head = (ring_head + 1) % fk
                cur_k += 1
                got = True
            if got and ring_len == fk:
                r_mean = fsum(ring[(ring_head + j) % fk] for j in range(fk)) / fk
        if pend is None:
            if d == 0:
                if rule == "z":
                    if isfinite(z) and isfinite(beta):
                        if z <= -P["entry_z"]:
                            pend = ("E", 1, beta, i)
                        elif z >= P["entry_z"]:
                            pend = ("E", -1, beta, i)
                else:
                    b = fixed if fixed is not None else beta
                    if isfinite(r_mean) and isfinite(b):
                        if abs(r_mean) * 1e4 * fh > cost(b):
                            pend = ("E", -1 if r_mean > 0 else 1, b, i)
            else:
                timed = P["max_hold"] > 0 and i - fill_in >= P["max_hold"]
                if rule == "z":
                    dz = d * z
                    by_rule = isfinite(z) and (dz >= -P["exit_z"] or dz <= -P["stop_z"])
                else:
                    edge = -d * r_mean * 1e4 * fh
                    by_rule = not (edge > cost(x_beta))
                if by_rule or timed:
                    pend = ("X", i)
    openp = None
    if d != 0:
        fi = fill_in
        iy = next((j for j in range(n - 1, fi - 1, -1) if isfinite(cy[j])), None)
        ix = next((j for j in range(n - 1, fi - 1, -1) if isfinite(cx[j])), None)
        ey, ex_ = (iy, cy[iy]) if iy is not None else (fi, pin_y), (ix, cx[ix]) if ix is not None else (fi, pin_x)
        (jy, cly), (jx, clx) = ey, ex_
        gross = qy * (cly - pin_y) + qx * (clx - pin_x)
        fee = abs(qy) * (pin_y * FEE + cly * FEE) + abs(qx) * (pin_x * FEE + clx * FEE)
        gb, fb = gross * 1e4, fund * 1e4
        openp = dict(dir=d, sig_in=lo + sig_in, fill_in=lo + fill_in, gross_bps=gb, fee_bps=fee, funding_bps=fb,
                     net_bps=gb - fee + fb, stale_min=(n - 1) - min(jy, jx), last_close_y=lo + jy, last_close_x=lo + jx,
                     pending_exit=(pend is not None and pend[0] == "X"), pend_sig=(lo + pend[1]) if pend is not None and pend[0] == "X" else None)
        cnt["open_at_end"] = 1
    else:
        cnt["open_at_end"] = 0
    return trades, openp, cnt


def window(a, Y, X):
    first = max(Y.first, X.first)
    last = min(Y.last, X.last)
    lo = max(first, a.from_day * DAY - a.window)
    hi = min(last, a.to_day * DAY - 1)
    return lo, hi, first, last


def fmt_min(m):
    s = G0_MS + m * MIN_MS
    import datetime
    return datetime.datetime.fromtimestamp(s / 1000, datetime.timezone.utc).strftime("%d.%m %H:%M")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="каталог сырья: <root>/<биржа>/{candles,funding}/<SYM>.csv.gz")
    ap.add_argument("--y-ex", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--x-ex", required=True)
    ap.add_argument("--x", required=True)
    ap.add_argument("--from-day", type=int, required=True)
    ap.add_argument("--to-day", type=int, required=True)
    ap.add_argument("--rule", choices=["z", "funding"], default="z")
    ap.add_argument("--window", type=int, required=True)
    ap.add_argument("--min-obs", type=int, required=True)
    ap.add_argument("--beta-fixed", type=float)
    ap.add_argument("--entry-z", type=float, default=0.0)
    ap.add_argument("--exit-z", type=float, default=0.0)
    ap.add_argument("--stop-z", type=float, default=math.inf)
    ap.add_argument("--max-hold", type=int, default=0)
    ap.add_argument("--funding-k", type=int, default=0)
    ap.add_argument("--hold-payments", type=int, default=0)
    ap.add_argument("--funding", action="store_true", help="фандинг включён в run (--funding-y / --funding-x)")
    ap.add_argument("--trades", required=True, help="csv --trades-out прогона ЭТОГО задания (одно задание, job=0)")
    ap.add_argument("--cell", help="метка клетки в trades/cells-out (по умолчанию — единственная)")
    ap.add_argument("--cells-out", help="csv --cells-out того же прогона (счётчики)")
    ap.add_argument("--open-out", help="csv --open-out (оценка открытой позиции)")
    ap.add_argument("--min-span", type=int, default=0)
    ap.add_argument("--tol", type=float, default=1e-6, help="допуск по bps")
    ap.add_argument("--show", type=int, default=3, help="сколько сделок напечатать подробно")
    a = ap.parse_args()

    Y = Leg(a.root, a.y_ex, a.y)
    X = Y if (a.x_ex, a.x) == (a.y_ex, a.y) else Leg(a.root, a.x_ex, a.x)
    lo, hi, first, last = window(a, Y, X)
    print(f"задание {a.y_ex}/{a.y} против {a.x_ex}/{a.x}: спан ног Y {fmt_min(Y.first)}..{fmt_min(Y.last)}, X {fmt_min(X.first)}..{fmt_min(X.last)}; "
          f"окно задания {fmt_min(lo)}..{fmt_min(hi)} ({hi - lo + 1} мин), ставок Y {len(Y.fund)}, X {len(X.fund)}; ставки в run: {'да' if a.funding else 'нет'}")
    P = dict(window=a.window, min_obs=a.min_obs, beta_fixed=a.beta_fixed, rule=a.rule, entry_z=a.entry_z, exit_z=a.exit_z,
             stop_z=a.stop_z, max_hold=a.max_hold, funding_k=a.funding_k, hold_payments=a.hold_payments)
    if a.rule == "z" and a.beta_fixed is None:
        pass
    if a.rule == "funding" and a.beta_fixed is None:
        P["beta_fixed"] = 1.0  # как run для funding-*: β = 1
    trades, openp, cnt = simulate(P, Y, X, lo, hi, a.funding)
    from_min = a.from_day * DAY
    kept = [t for t in trades if t["fill_in"] >= from_min]
    pre = len(trades) - len(kept)

    rows = list(csv.DictReader(open(a.trades, encoding="utf-8")))
    if a.cell:
        rows = [r for r in rows if r["cell"] == a.cell]
    bad = 0
    if len(rows) != len(kept):
        print(f"ОШИБКА: сделок в csv {len(rows)}, пересчитано {len(kept)} (до окна отброшено {pre})")
        bad += 1
    worst = 0.0
    for r, t in zip(rows, kept):
        ints = all(int(r[k]) == t[k] for k in ("dir", "sig_in", "fill_in", "sig_out", "fill_out"))
        diffs = [abs(float(r[k]) - t[k]) for k in ("gross_bps", "fee_bps", "funding_bps", "net_bps")]
        dm = max(diffs)
        worst = max(worst, dm)
        if not ints or dm > a.tol:
            bad += 1
            if bad <= 5:
                print(f"ОШИБКА сделка dir={r['dir']} sig_in={r['sig_in']}: целые совпали={ints}, max|Δ|={dm:.3e}; csv net {r['net_bps']} руками {t['net_bps']!r}")
    nz = sum(1 for t in kept if t["funding_bps"] != 0.0)
    print(f"сделок в csv {len(rows)}, пересчитано {len(kept)} (до окна отброшено {pre}); с ненулевым фандингом {nz}; "
          f"расхождений {bad}; max|Δ| по gross/fee/funding/net {worst:.3e} bps")

    # подробно: первая long, первая short, затем с фандингом / с отложенным выходом
    shown, picks = set(), []
    for pick in (lambda t: t["dir"] == 1, lambda t: t["dir"] == -1, lambda t: t["funding_bps"] != 0.0,
                 lambda t: t["fill_out"] - t["sig_out"] > 1):
        for j, t in enumerate(kept):
            if j not in shown and pick(t):
                shown.add(j)
                picks.append(j)
                break
    for j in picks[: a.show]:
        t, r = kept[j], rows[j] if j < len(rows) else None
        implied = ""
        if a.rule == "z" and r is not None:
            gr = float(r["gross_bps"]) / 1e4
            dxo = (t["pout_x"] - t["pin_x"]) / t["pin_x"]
            # gross = dir/pin_y·(pout_y−pin_y) − dir·β·(pout_x−pin_x)/pin_x  ⇒  β = (dir·ΔY/pinY − gross)/(dir·ΔX/pinX)
            dyo = (t["pout_y"] - t["pin_y"]) / t["pin_y"]
            if dxo != 0:
                implied = f"; β из csv.gross = {(t['dir'] * dyo - gr) / (t['dir'] * dxo):.9f}"
        print(f"  сделка #{j}: dir={t['dir']:+d} сигнал {fmt_min(t['sig_in'])} вход {fmt_min(t['fill_in'])} сигнал выхода {fmt_min(t['sig_out'])} выход {fmt_min(t['fill_out'])} "
              f"(мин {t['fill_in']}..{t['fill_out']}); β={t['beta']:.9f}{implied}; open Y {t['pin_y']!r}→{t['pout_y']!r}, X {t['pin_x']!r}→{t['pout_x']!r}; "
              f"gross {t['gross_bps']:.6f} fee {t['fee_bps']:.6f} fund {t['funding_bps']:.6f} net {t['net_bps']:.6f}"
              + (f" | csv net {float(r['net_bps']):.6f}" if r is not None else ""))

    # счётчики
    if a.cells_out:
        cr = list(csv.DictReader(open(a.cells_out, encoding="utf-8")))
        cr = [r for r in cr if not a.cell or r["cell"] == a.cell][0]
        want = dict(entries=cnt["entries"], entry_deferred=cnt["entry_deferred"], exit_deferred=cnt["exit_deferred"],
                    gap_minutes=cnt["gap_minutes"], minutes_in_pos=cnt["minutes_in_pos"], open_at_end=cnt["open_at_end"],
                    trades=cnt["trades"], pre_window=pre)
        cmpd = {k: (int(cr[k]), v) for k, v in want.items()}
        diff = {k: v for k, v in cmpd.items() if v[0] != v[1]}
        print("счётчики run / руками: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in cmpd.items()))
        if diff:
            bad += 1
            print("ОШИБКА счётчики:", diff)
        exits = sum(t["fill_out"] - t["sig_out"] - 1 for t in trades)
        print(f"  отложенные минуты выхода: по сделкам Σ(fill_out − sig_out − 1) = {exits}; в счётчике {cr['exit_deferred']} "
              f"(разница {int(cr['exit_deferred']) - exits} мин — ожидание выхода открытой позиции на конце окна)")
        if cr.get("mtm_n") not in (None, "nan"):
            print(f"  оценка открытой по cells-out: mtm_n={cr['mtm_n']} net={cr['mtm_net_bps']} gross={cr['mtm_gross_bps']} fee={cr['mtm_fee_bps']} fund={cr['mtm_funding_bps']} stale_max={cr['mtm_stale_max_min']}")
    if openp:
        print(f"открытая позиция на конце окна (руками): dir={openp['dir']:+d} сигнал {fmt_min(openp['sig_in'])} вход {fmt_min(openp['fill_in'])}; "
              f"последний close Y {fmt_min(openp['last_close_y'])}, X {fmt_min(openp['last_close_x'])}; давность {openp['stale_min']} мин"
              f"{'; выход уже назначен на сигнале ' + fmt_min(openp['pend_sig']) + ', но исполнить нечем (нет open)' if openp['pending_exit'] else ''}")
        print(f"  оценка руками: gross {openp['gross_bps']:.6f} fee {openp['fee_bps']:.6f} funding {openp['funding_bps']:.6f} net {openp['net_bps']:.6f}")
        if a.open_out:
            orows = [r for r in csv.DictReader(open(a.open_out, encoding="utf-8")) if not a.cell or r["cell"] == a.cell]
            if len(orows) != 1:
                bad += 1
                print(f"ОШИБКА: в open-out строк {len(orows)}, ожидалась одна")
            else:
                r = orows[0]
                df = max(abs(float(r[k]) - openp[k]) for k in ("gross_bps", "fee_bps", "funding_bps", "net_bps"))
                ok = df <= a.tol and int(r["stale_min"]) == openp["stale_min"] and int(r["fill_in"]) == openp["fill_in"] and int(r["dir"]) == openp["dir"]
                bad += not ok
                print(f"  {'OK ' if ok else 'ОШИБКА'} open-out: net csv {float(r['net_bps']):.6f}, stale csv {r['stale_min']}, max|Δ| {df:.3e}")
    elif a.open_out:
        orows = [r for r in csv.DictReader(open(a.open_out, encoding="utf-8")) if not a.cell or r["cell"] == a.cell]
        if orows:
            bad += 1
            print(f"ОШИБКА: в open-out {len(orows)} строк, а руками позиция на конце закрыта")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
