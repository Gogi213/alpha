"""Эталон П-40 (TK-501), шаг 3: вход мейкером по bid/ask с моделью очереди prob:3, жизнь заявки, страховка, выход.
Вход: book2_<A|B>.npz (book.py с журналом уровней), grid.npz и episodes_<theta>.csv (episodes.py), сделки trades_<A|B>.npz
(t_ms, price_tick, qty_base, taker_sell). Один эпизод — независимая симуляция (заявки не влияют на рынок, N=1000 USDT).
Толкования протокола — в отчёте. Очередь — мой разбор prob:3 (f(x)=x^3), не код hftbacktest."""
import sys
import numpy as np

SC = 1e6
N_USDT = 1000.0
MAKER_BPS = 1.26
TAKER_BPS = 3.15
T_LIFE_MS = 60_000
L_MS = 500


class Leg:
    """Одна биржа: BBO по событиям + журнал уровней + сделки."""

    def __init__(self, book, trades):
        z = np.load(book)
        self.t, self.o = z["t"], z["o"]
        self.lt, self.ls, self.lp, self.lq = z["lt"], z["ls"], z["lp"], z["lq"]
        tr = np.load(trades)
        self.tt, self.tp, self.tq, self.ts = tr["t"], tr["p"], tr["q"], tr["sell"]

    def bbo(self, t):
        i = np.searchsorted(self.t, t, side="right") - 1
        return self.o[i, 0], self.o[i, 1]

    def level_qty(self, side, tick, t):
        """Количество на уровне после последней строки журнала со временем <= t (0, если строк нет)."""
        n = np.searchsorted(self.lt, t, side="right")
        m = (self.ls[:n] == side) & (self.lp[:n] == tick)
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            return 0.0
        # последний снимок обнуляет всё, чего в нём нет: считаем последнюю строку уровня, но не старше последнего снимка
        return float(self.lq[idx[-1]])


def f3(x):
    return x ** 3


def simulate_order(leg, side, tick, qty, t_arr, t_cancel, t_end):
    """side 0 = покупка (бид-очередь), 1 = продажа. Заявка встаёт в t_arr, действует до t_cancel (вступление отмены), данные до t_end.
    Возвращает (t_fill или -1, price_tick, filled_frac, immediate). Заявка на мейкере; сквозь исполнение (противоположная
    лучшая цена достигла нашей) — исполнение по цене заявки сразу."""
    bb, ba = leg.bbo(t_arr)
    if (side == 0 and ba <= tick) or (side == 1 and bb >= tick):
        return t_arr, tick, 1.0, True
    prev = leg.level_qty(side, tick, t_arr)
    front = prev
    leaves = qty
    # события после t_arr: строки журнала уровня, сделки, BBO-сквозное
    i0 = np.searchsorted(leg.lt, t_arr, side="right")
    i1 = np.searchsorted(leg.lt, t_cancel, side="right")
    lm = np.flatnonzero((leg.ls[i0:i1] == side) & (leg.lp[i0:i1] == tick)) + i0
    j0 = np.searchsorted(leg.tt, t_arr, side="right")
    j1 = np.searchsorted(leg.tt, t_cancel, side="right")
    tm = np.arange(j0, j1)
    # хронология: объединение (время, источник 0 = журнал, 1 = сделка), стабильно
    ev = [(leg.lt[i], 0, i) for i in lm] + [(leg.tt[j], 1, j) for j in tm]
    ev.sort(key=lambda e: (e[0], e[1]))
    for (t, kind, idx) in ev:
        if kind == 0:
            new = leg.lq[idx]
            if new < prev:
                chg = prev - new
                back = prev - front
                if front <= 0 and back <= 0:
                    p = 1.0
                else:
                    ff, bf = f3(max(front, 0.0)), f3(max(back, 0.0))
                    p = ff / (ff + bf) if ff + bf > 0 else 1.0
                est = front - (1.0 - p) * chg + min(back - p * chg, 0.0)
                front = min(est, new)
            else:
                front = min(front, new)
            prev = new
        else:
            tp, tq, ts = leg.tp[idx], leg.tq[idx], leg.ts[idx]
            # покупка заполняется продавцами-тейкерами по цене <= нашей; продажа — покупателями по цене >= нашей
            if side == 0 and ts and tp <= tick:
                if tp < tick:
                    front = min(front, 0.0)
                front -= tq
            elif side == 1 and (not ts) and tp >= tick:
                if tp > tick:
                    front = min(front, 0.0)
                front -= tq
            else:
                continue
            if front < 0:
                leaves -= min(leaves, -front)
                front = 0.0
                if leaves <= 1e-9:
                    return t, tick, 1.0, False
    return -1, tick, 1.0 - leaves / qty, False


def run_episode(A, B, g, row, theta):
    t_s, dr, d_in, t_e = int(row[0]), row[1], row[2], row[3]
    T, d1, ok = g["T"], g["d1"], g["ok"]
    end_data = max(A.t[-1], B.t[-1])
    # дорогая нога: dr > 0 -> A дорога (продаём A, покупаем B)
    sell_leg, buy_leg = (A, B) if dr > 0 else (B, A)
    # цены выбираются в t_s (момент решения): продажа на аск дорогой, покупка на бид дешёвой
    _, ask_s = sell_leg.bbo(t_s)
    bid_b, _ = buy_leg.bbo(t_s)
    ps, pb = int(ask_s), int(bid_b)
    t_arr = t_s + L_MS
    # снятие: dir*d <= theta/2 (решение в моменте, действует +L_MS) или T_life от t_s
    k0 = np.searchsorted(T, t_s, side="right")
    half = np.flatnonzero(ok[k0:] & (dr * d1[k0:] <= 0.5 * theta))
    t_half = T[k0 + half[0]] if len(half) else end_data
    t_dec = min(t_half, t_s + T_LIFE_MS)
    t_cancel = t_dec + L_MS
    qs = N_USDT / (ps / SC)
    qb = N_USDT / (pb / SC)
    fs = simulate_order(sell_leg, 1, ps, qs, t_arr, t_cancel, end_data)
    fb = simulate_order(buy_leg, 0, pb, qb, t_arr, t_cancel, end_data)
    out = dict(t_start=t_s, dir=dr, d_in=d_in, t_conv_mid=t_e, p_sell=ps, p_buy=pb,
               t_fill_sell=fs[0], t_fill_buy=fb[0], frac_sell=fs[1 + 1], frac_buy=fb[1 + 1],
               imm_sell=fs[3], imm_buy=fb[3], t_cancel=t_cancel)
    both = fs[0] >= 0 and fb[0] >= 0
    one = (fs[0] >= 0) != (fb[0] >= 0)
    out["outcome"] = "both" if both else ("one" if one else "none")
    out.update(exit_t=-1, exit_kind="", pnl_bps=np.nan, fees_bps=np.nan)
    if both:
        # выход: заявки при dir*d <= 0.5*d_in, лучшая цена своей стороны: выкуп проданной ноги по биду, продажа купленной по аску
        t_both = max(fs[0], fb[0])
        kk = np.searchsorted(T, t_both, side="right")
        hit = np.flatnonzero(ok[kk:] & (dr * d1[kk:] <= 0.5 * abs(d_in)))
        if len(hit) == 0:
            out["exit_kind"] = "no_exit_signal"
            return out
        t_x = T[kk + hit[0]]
        bs, _ = sell_leg.bbo(t_x)
        _, ab = buy_leg.bbo(t_x)
        xs, xb = int(bs), int(ab)      # buy back sold leg at bid; sell bought leg at ask
        rs = simulate_order(sell_leg, 0, xs, N_USDT / (xs / SC), t_x + L_MS, end_data + 1, end_data)
        rb = simulate_order(buy_leg, 1, xb, N_USDT / (xb / SC), t_x + L_MS, end_data + 1, end_data)
        if rs[0] >= 0 and rb[0] >= 0:
            out["exit_t"] = max(rs[0], rb[0])
            out["exit_kind"] = "maker" + ("_after_mid" if (t_e > 0 and max(rs[0], rb[0]) > t_e) else "")
            pnl = ((fs[1] - xs) / fs[1] + (xb - fb[1]) / fb[1]) * 1e4
            out["pnl_bps"] = pnl - 4 * MAKER_BPS
            out["fees_bps"] = 4 * MAKER_BPS
        else:
            out["exit_kind"] = "exit_unfilled_censored"
    elif one:
        # страховка: исполненная нога закрывается мейкером ещё T_LIFE; иначе тейкер
        leg_sell_done = fs[0] >= 0
        t_f = fs[0] if leg_sell_done else fb[0]
        t_c = max(t_f, t_cancel)
        if leg_sell_done:
            bsx, _ = sell_leg.bbo(t_c)
            xp = int(bsx)
            r = simulate_order(sell_leg, 0, xp, N_USDT / (xp / SC), t_c + L_MS, t_c + T_LIFE_MS + L_MS, end_data)
        else:
            _, abx = buy_leg.bbo(t_c)
            xp = int(abx)
            r = simulate_order(buy_leg, 1, xp, N_USDT / (xp / SC), t_c + L_MS, t_c + T_LIFE_MS + L_MS, end_data)
        if r[0] >= 0:
            out["exit_t"] = r[0]
            out["exit_kind"] = "insurance_maker"
            fee = 2 * MAKER_BPS
        else:
            t_t = t_c + L_MS + T_LIFE_MS + L_MS
            if leg_sell_done:
                _, a2 = sell_leg.bbo(t_t)
                xp = int(a2)
            else:
                b2, _ = buy_leg.bbo(t_t)
                xp = int(b2)
            out["exit_t"] = t_t
            out["exit_kind"] = "insurance_taker"
            fee = MAKER_BPS + TAKER_BPS
        if leg_sell_done:
            pnl = (fs[1] - xp) / fs[1] * 1e4
        else:
            pnl = (xp - fb[1]) / fb[1] * 1e4
        out["pnl_bps"] = pnl - fee
        out["fees_bps"] = fee
    return out


def main(outdir, theta):
    A = Leg(f"{outdir}/book2_binance.npz", f"{outdir}/trades_binance.npz")
    B = Leg(f"{outdir}/book2_okx.npz", f"{outdir}/trades_okx.npz")
    g = np.load(f"{outdir}/ep/grid.npz")
    eps = np.loadtxt(f"{outdir}/ep/episodes_{theta}.csv", delimiter=",", skiprows=1, ndmin=2)
    rows = [run_episode(A, B, g, r, float(theta)) for r in eps]
    import csv
    with open(f"{outdir}/ep/maker_{theta}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print("theta", theta, "n", len(rows), Counter(r["outcome"] for r in rows), Counter(r["exit_kind"] for r in rows))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
