"""Эталон П-40 (TK-501), шаг 4: признаки §3.5 на момент начала эпизода t_start (только прошлое, строго < t).
Вход: book2_<A|B>.npz, trades_*.npz, grid.npz, episodes_<theta>.csv, maker_<theta>.csv, series_<A|B>.npz (series.py:
mark/index/funding/OI/ликвидации). Выход: features_<theta>.csv — сырые значения + терцили (по эпизодам этой выборки;
границы терцилей на D1 фиксируются для D2 в ядре — здесь только 1 пара×1 сутки, терцили описательны).
Пропуск = nan / -1. Толкования — в отчёте p40-ref-episodes."""
import sys
import numpy as np

SC = 1e6
N_USDT = 1000.0


def asof(t_arr, t):
    return np.searchsorted(t_arr, t, side="left") - 1  # строго до t


def terc(x):
    """Терцили 0/1/2 по выборке; nan -> -1. Границы — 33,3 и 66,7 % по не-nan."""
    out = np.full(len(x), -1, dtype=int)
    m = ~np.isnan(x)
    if m.sum() < 3:
        return out
    q1, q2 = np.quantile(x[m], [1 / 3, 2 / 3])
    out[m] = np.where(x[m] <= q1, 0, np.where(x[m] <= q2, 1, 2))
    return out


def mid_at(t_book, o, t):
    i = asof(t_book, t)
    return (o[i, 0] + o[i, 1]) / 2.0


def realized_vol(t_book, o, t, win_ms=300_000, step_ms=1000):
    """Реализованная вол mid за прошлые 5 мин: sd приращений ln mid на сетке 1 с, bps."""
    g = np.arange(t - win_ms, t, step_ms)
    g = g[g > t_book[0]]
    if len(g) < 10:
        return np.nan
    i = np.searchsorted(t_book, g, side="right") - 1
    m = (o[i, 0] + o[i, 1]) / 2.0
    return float(np.std(np.diff(np.log(m))) * 1e4)


def flow_imb(tr, t, win_ms=60_000):
    """(покупки − продажи)/(сумма) по номиналу за прошлую 1 мин; taker_sell = is_buyer_maker."""
    tt, tp, tq, ts = tr
    a, b = np.searchsorted(tt, t - win_ms, "left"), np.searchsorted(tt, t, "left")
    if b <= a:
        return np.nan
    v = tq[a:b] * tp[a:b] / SC
    sell = ts[a:b]
    s, bu = v[sell].sum(), v[~sell].sum()
    return float((bu - s) / (bu + s)) if bu + s > 0 else np.nan


def features(A, B, aux, grid, ep, mk):
    """A,B: dict book t,o,trades; aux: dict A,B; grid: T,d1,x,lvl; ep: строка episodes (t_start, dir, d_in, idx_start)."""
    t, dr, d_in, i0 = int(ep[0]), ep[1], ep[2], int(ep[12])
    ia, ib = int(grid["ia"][i0]), int(grid["ib"][i0])
    oa, ob = A["o"][ia], B["o"][ib]
    f = {"t_start": t, "dir": dr, "d_in": abs(d_in)}
    # глубина на N: худшая из двух ног по VWAP-проскальзыванию набора N (от лучшей цены), bps; чем больше, тем тоньше
    def slip(o, side):
        v = o[4] if side == 0 else o[5]
        best = o[0] if side == 0 else o[1]
        return abs(v * SC - best) / best * 1e4 if v > 0 else np.nan
    # дорогая нога (продаём) = A при dir>0 (d=ln(A/B)>0), иначе B
    sell_leg, buy_leg = (oa, ob) if dr > 0 else (ob, oa)
    f["depth_slip"] = np.nanmax([slip(sell_leg, 0), slip(buy_leg, 1)])
    f["spread"] = max((oa[1] - oa[0]) / ((oa[0] + oa[1]) / 2), (ob[1] - ob[0]) / ((ob[0] + ob[1]) / 2)) * 1e4
    f["vol5m"] = np.nanmean([realized_vol(A["t"], A["o"], t), realized_vol(B["t"], B["o"], t)])
    f["plevel"] = float(grid["lvl"][i0] * 1e4)
    # OI за 5 мин: относительное изменение суммарной стоимости OI двух ног (последнее значение до t против t−5 мин)
    def oi_chg(a):
        ot, ov = a["oi_t"], a["oi_v"]
        j1, j0 = asof(ot, t), asof(ot, t - 300_000)
        if j1 < 0 or j0 < 0 or j1 == j0:
            return np.nan
        return (ov[j1] - ov[j0]) / ov[j0]
    f["oi5m"] = np.nanmean([oi_chg(aux["A"]), oi_chg(aux["B"])])
    f["liq5m"] = int(sum(((a["liq_t"] >= t - 300_000) & (a["liq_t"] < t)).sum() for a in (aux["A"], aux["B"])) > 0)
    fa, fb = flow_imb(A["tr"], t), flow_imb(B["tr"], t)
    # знак потока относительно направления позиции: >0 — давление «в сторону расхождения» (покупают дорогую ногу)
    f["flow1m"] = np.nanmean([fa * dr, fb * (-dr)])
    def bimb(o):
        return (o[6] - o[7]) / (o[6] + o[7]) if o[6] + o[7] > 0 else np.nan
    f["bookimb"] = np.nanmean([bimb(oa) * dr, bimb(ob) * (-dr)])
    # лидер за 10 с: нога с большим |Δln mid| за [t−10 с, t); A=1, B=0, равно/нет данных = -1
    ra = np.log(mid_at(A["t"], A["o"], t) / mid_at(A["t"], A["o"], t - 10_000))
    rb = np.log(mid_at(B["t"], B["o"], t) / mid_at(B["t"], B["o"], t - 10_000))
    f["leader"] = -1 if abs(ra) == abs(rb) else int(abs(ra) > abs(rb))
    f["hour"] = int(((t // 1000) % 86400) // 3600 // 6)
    # Φ_t: n_s·(r_A − r_B), знак для позиции «продаём A / покупаем B»; r — последняя известная ставка ноги
    H = 600_000
    ra_f, rb_f = last_rate(aux["A"], t), last_rate(aux["B"], t)
    ns = int(settle_within(aux["A"], t, H) or settle_within(aux["B"], t, H))
    f["phi_bps"] = ns * (ra_f - rb_f) * 1e4 if not (np.isnan(ra_f) or np.isnan(rb_f)) else np.nan
    f["n_s"] = ns
    f["rA"], f["rB"] = ra_f, rb_f
    return f


def last_rate(a, t):
    j = asof(a["fr_t"], t)
    return float(a["fr_v"][j]) if j >= 0 else np.nan


def settle_within(a, t, H):
    """Есть ли расчёт ноги в (t, t+H]: next_funding_time из последней записи до t."""
    j = asof(a["fr_t"], t)
    if j < 0 or np.isnan(a["fr_next"][j]):
        return False
    return t < a["fr_next"][j] <= t + H


def load_aux(path):
    z = np.load(path)
    return {k: z[k] for k in z.files}


def main(outdir, theta):
    A = dict(np.load(f"{outdir}/book2_binance.npz"))
    B = dict(np.load(f"{outdir}/book2_okx.npz"))
    for X, n in ((A, "binance"), (B, "okx")):
        z = np.load(f"{outdir}/trades_{n}.npz")
        X["tr"] = (z["t"], z["p"], z["q"], z["sell"])
    aux = {"A": load_aux(f"{outdir}/series_binance.npz"), "B": load_aux(f"{outdir}/series_okx.npz")}
    grid = np.load(f"{outdir}/ep/grid.npz")
    eps = np.loadtxt(f"{outdir}/ep/episodes_{theta}.csv", delimiter=",", skiprows=1, ndmin=2)
    rows = [features(A, B, aux, grid, e, None) for e in eps]
    keys = list(rows[0].keys())
    cols = {k: np.array([r[k] for r in rows], dtype=float) for k in keys}
    # терцили (по этой выборке) для порядковых
    for src, dst in (("depth_slip", "f_depth"), ("spread", "f_spread"), ("vol5m", "f_vol5m"), ("plevel", "f_plevel"),
                     ("oi5m", "f_oi5m"), ("flow1m", "f_flow1m"), ("bookimb", "f_bookimb"), ("d_in", "f_d_arm")):
        cols[dst] = terc(cols[src]).astype(float)
    cols["f_phi_sign"] = np.where(np.isnan(cols["phi_bps"]), -1.0, (cols["phi_bps"] > 0).astype(float))
    cols["f_liq"] = cols["liq5m"]
    cols["f_leader"] = cols["leader"]
    cols["f_hour"] = cols["hour"]
    names = list(cols.keys())
    with open(f"{outdir}/ep/features_{theta}.csv", "w") as f:
        f.write(",".join(names) + "\n")
        for i in range(len(eps)):
            f.write(",".join(f"{cols[k][i]:.10g}" for k in names) + "\n")
    print("theta", theta, "n", len(eps), {k: int(np.isnan(cols[k]).sum()) for k in keys if np.isnan(cols[k]).any()})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
