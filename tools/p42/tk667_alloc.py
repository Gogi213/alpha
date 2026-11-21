#!/usr/bin/env python3
"""TK-667: размер лонга и шорта (и сделки) на ГОТОВЫХ сделках П-42 — 775 контрактов, 01.07–13.11, 500 $, после комиссий, без фандинга. Движок не запускается (В-213).
Правила веса — фиксированный список, записан ДО счёта: P-42 §13.67 (`PS-935`, `docs/findings/p42-allocation-775-2026-11-21/prereg.md`): (a) 500 $; (b) равный убыток на стоп; (c) вес стороны ∝ 1/σ суточного P&L
(+ (c-causal): σ только из суток раньше входа, первые 14 суток 1:1); (d) размер ∝ 1/волатильность монеты (std лог-доходностей 1m за 1 440 минут строго до входа, обрезка [1/3; 3]) + (d0) без обрезки.
Пары: P1 = L_base + S_base, P2 = L_n24 + S_base, P3 = L_n24 + A_n_1 (эталон). Все правила нормируются на депозит D1 правила (a) (метод TK-664: 250 $ × максимум одновременных позиций, с весами).
Те же загрузчики и сверка n / Σ, что у tk663_shape.py / tk664_funding_capital.py; сверка (a) с TK-663 / TK-664 — расхождение = остановка.
Запуск на srv: /opt/venv/bin/python tk667_alloc.py <код TK-639 (/data/p42-tk639/code)> <каталог выхода>
Выход: tables.md, summary.json, perday-<пара>.csv (суточный P&L по закрытию при W_i), trades-<пара>.csv (сделки и веса). Только описание, вердикта нет."""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk663_shape as t63  # noqa: E402  (load_lines с проверкой n / Σ против TK-662, uw, sharpe, num, pct, table)

num, pct, table, sharpe, uw = t63.num, t63.pct, t63.table, t63.sharpe, t63.uw

NOTIONAL = 500.0
MARGIN = NOTIONAL / 2.0                 # 250 $ на позицию при плече 2
MIN_MS = 60_000
DAY_MS = 86_400_000
VOL_MIN = 1440                          # окно волатильности, минут
VOL_NEED = 720                          # минимум доходностей в окне
CLIP = (1.0 / 3.0, 3.0)
WARM = 14                               # (c-causal): первые 14 суток 1:1
NBOOT = 2000
SEED = 667
KL_ROOTS = ["/data/p42-tk596/jul/klines", "/data/p42-tk596/aug/klines", "/data/klines/binance"]
NEED = ("S_base", "A_n_1", "L_base", "L_n24")
PAIRS = {"P1": ("L_base", "S_base"), "P2": ("L_n24", "S_base"), "P3": ("L_n24", "A_n_1")}
RULES = [("a", "(a) 500 $"), ("b", "(b) равный стоп"), ("c", "(c) 1/σ суток"), ("cc", "(c-causal)"), ("d", "(d) 1/вол. монеты"), ("d0", "(d0) без обрезки")]
# сверка правила (a) с TK-663 / TK-664 (до фандинга): Σ пары, Шарп по дню входа, D1, зазор, просадка
CHECK = {"P1": dict(sum=416.31, sharpe_in=3.74, d1=1250.0, gap=14, maxdd=77.64), "P3": dict(sum=1312.00, sharpe_in=7.77, d1=1000.0, gap=24, maxdd=153.44)}


# ───────────────────────────── волатильность монеты ─────────────────────────────

def kline_path(sym, day):
    for r in KL_ROOTS:
        p = os.path.join(r, sym, day.strftime("%Y-%m-%d") + ".csv")
        if os.path.exists(p):
            return p
    return None


def vol24(sym, t_in):
    """std лог-доходностей close/close соседних 1m свечей за 1 440 минут строго до минуты входа; NaN — меньше VOL_NEED доходностей. t_in — массив мс."""
    m = (t_in // MIN_MS) * MIN_MS
    d_lo = dt.datetime.fromtimestamp((m.min() - VOL_MIN * MIN_MS) // DAY_MS * DAY_MS / 1000, dt.timezone.utc).date()
    d_hi = dt.datetime.fromtimestamp((m.max() - MIN_MS) // DAY_MS * DAY_MS / 1000, dt.timezone.utc).date()
    ot, cl = [], []
    d = d_lo
    while d <= d_hi:
        p = kline_path(sym, d)
        if p:
            k = pd.read_csv(p, usecols=["open_time", "close"])
            ot.append(k.open_time.to_numpy(np.int64))
            cl.append(k.close.to_numpy(float))
        d += dt.timedelta(days=1)
    out = np.full(len(t_in), np.nan)
    if not ot:
        return out
    ot, cl = np.concatenate(ot), np.concatenate(cl)
    o = np.argsort(ot, kind="stable")
    ot, cl = ot[o], cl[o]
    ok = (cl > 0) & (np.diff(ot, prepend=ot[0] - 2 * MIN_MS) > 0)   # без повторов минуты
    ot, cl = ot[ok], cl[ok]
    gap = np.concatenate([[False], np.diff(ot) == MIN_MS])           # доходность i — между свечами i−1 и i, если они соседние
    ret = np.zeros(len(ot))
    ret[1:] = np.log(cl[1:] / cl[:-1])
    for j, mj in enumerate(m):
        a = int(np.searchsorted(ot, mj - VOL_MIN * MIN_MS, side="left"))
        b = int(np.searchsorted(ot, mj, side="left"))           # свечи с open_time < mj
        g = gap[a:b].copy()
        if len(g):
            g[0] = False                                      # первая свеча окна — без предыдущей внутри окна
        r = ret[a:b][g]
        if len(r) >= VOL_NEED:
            out[j] = float(np.std(r, ddof=1))
    return out


def add_vol(df):
    v = np.full(len(df), np.nan)
    for s, ix in df.groupby("sym").indices.items():
        v[ix] = vol24(s, df.t_in.to_numpy(np.int64)[ix])
    return v


# ───────────────────────────── капитал с весами ─────────────────────────────

def sweep_w(t_in, t_out, w, pnl_w):
    """Развёртка событий [вход, выход), выход раньше входа в ту же мс. Возвращает: D1 = 250 $ · max Σ весов открытых, D2 = max(0; max(250 $ · Σ весов − реализованный P&L)), max число позиций."""
    n = len(t_in)
    assert (t_out > t_in).all(), "сделка нулевой длины"
    t = np.concatenate([t_in, t_out])
    d = np.concatenate([np.ones(n), -np.ones(n)])
    ww = np.concatenate([w, w])
    pp = np.concatenate([np.zeros(n), pnl_w])
    o = np.lexsort((d, t))
    t, d, ww, pp = t[o], d[o], ww[o], pp[o]
    c = np.cumsum(d * ww)
    cnt = np.cumsum(d)
    assert abs(c[-1]) < 1e-6 and cnt.min() >= 0 and cnt[-1] == 0
    real = np.cumsum(pp)
    return {"d1": float(MARGIN * c.max()), "d2": float(max(0.0, (MARGIN * c - real).max())), "maxpos": int(cnt.max())}


# ───────────────────────────── веса ─────────────────────────────

def inv_weights(m):
    """w_x ∝ 1/m_x, Σ w = 2 (две стороны): {сторона: вес}."""
    r = {k: 1.0 / v for k, v in m.items()}
    s = sum(r.values())
    return {k: 2.0 * v / s for k, v in r.items()}


def build_weights(lk, sk, T, nd, vol, rr):
    """Сырые веса (до нормировки на депозит) всех правил для пары: {правило: массив по строкам объединённой таблицы T[lk] + T[sk]}."""
    a, b = T[lk], T[sk]
    both = pd.concat([a, b], ignore_index=True)
    side = both.side.to_numpy()
    n = len(both)
    W = {"a": np.ones(n)}
    # (b) равный убыток на стоп: ∝ 1/|средний убыток стопа линии|
    stop = {}
    for nm, df in ((lk, a), (sk, b)):
        s = rr.rr_stats([dict(pnl=p, kind=q) for p, q in zip(df.pnl, df.kind)])["stops"]
        stop[nm] = (abs(s["mean_loss"]), s["n"])
    wb = inv_weights({lk: stop[lk][0], sk: stop[sk][0]})
    W["b"] = np.where(side == "L", wb[lk], wb[sk]).astype(float)
    # (c) 1/σ суточного P&L (по закрытию, сутки 0…nd−1 пары)
    sd = {nm: np.bincount(df.day_out, weights=df.pnl, minlength=nd) for nm, df in ((lk, a), (sk, b))}
    sig = {nm: float(np.std(x, ddof=1)) for nm, x in sd.items()}
    wc = inv_weights(sig)
    W["c"] = np.where(side == "L", wc[lk], wc[sk]).astype(float)
    # (c-causal): σ из суток 0…d−1 (закрытия строго раньше суток входа); d < WARM — 1:1
    cum = {nm: (np.cumsum(x), np.cumsum(x * x)) for nm, x in sd.items()}

    def sig_before(nm, d):
        c1, c2 = cum[nm]
        n_ = d
        s1, s2 = c1[d - 1], c2[d - 1]
        return float(np.sqrt(max(0.0, (s2 - s1 * s1 / n_) / (n_ - 1))))
    din = ((both.t_in.to_numpy(np.int64) - D0_MS) // DAY_MS).astype(int)
    assert (din == both.day_in.to_numpy()).all(), "день входа ≠ ряд splice"
    wcc = np.ones(n)
    for i in range(n):
        d = int(din[i])
        if d < WARM:
            continue
        sl, ss = sig_before(lk, d), sig_before(sk, d)
        if sl > 0 and ss > 0:
            w2 = inv_weights({lk: sl, sk: ss})
            wcc[i] = w2[lk] if side[i] == "L" else w2[sk]
    W["cc"] = wcc
    # (d) 1/волатильность монеты: v_ref = медиана v_i по сделкам пары; нет v → вес 1
    v = np.concatenate([vol[lk], vol[sk]])      # порядок как в both: сначала лонг, потом шорт
    ok = v > 0
    vref = float(np.median(v[ok]))
    raw = np.ones(n)
    raw[ok] = vref / v[ok]
    W["d0"] = raw
    W["d"] = np.where(ok, np.clip(raw, *CLIP), 1.0)
    info = {"stop": {k: {"mean_loss": v_[0], "n": v_[1]} for k, v_ in stop.items()}, "w_b": wb, "sigma": sig, "w_c": wc, "vref": vref,
            "d_no_vol_share": float((~ok).mean()), "d_clipped_share": float((ok & ((raw < CLIP[0]) | (raw > CLIP[1]))).mean()),
            "cc_warm_share": float((din < WARM).mean())}
    return both, W, info


# ───────────────────────────── метрики ─────────────────────────────

def metrics(both, w, k, nd, NW, rr, hedge=None, wm=None):
    """При весах W = k · w: Σ, Шарп, просадка, зазоры, R:R, стопы, D1 / D2. hedge — P&L хеджа по сделкам при w (масштабируется k, входит в P&L сделки); wm — вес маржи (по умолчанию w)."""
    Wt = w * k
    p = (both.pnl.to_numpy() * w + (0.0 if hedge is None else hedge)) * k
    s_out = np.bincount(both.day_out.to_numpy(), weights=p, minlength=nd)
    s_in = np.bincount(both.day_in.to_numpy(), weights=p, minlength=NW)
    u = uw(s_out)
    r = rr.rr_stats([dict(pnl=x, kind=q) for x, q in zip(p, both.kind)])
    side = both.side.to_numpy()
    stp = both.kind.isin(t63.STOP_KINDS).to_numpy()
    by = {}
    for sd in ("L", "S"):
        m = stp & (side == sd)
        by[sd] = {"n": int(m.sum()), "mean": float(p[m].mean()) if m.any() else float("nan")}
    cap = sweep_w(both.t_in.to_numpy(np.int64), both.t_out.to_numpy(np.int64), (w if wm is None else wm) * k, p)
    return {"n": len(p), "sum": float(p.sum()), "mean": float(p.mean()), "sharpe_out": sharpe(s_out), "sharpe_in": sharpe(s_in), "maxdd": u["maxdd"], "sum_dd": float(p.sum() / u["maxdd"]) if u["maxdd"] > 0 else float("nan"),
            "gap_longest": u["gap_longest"], "gap_mean": u["gap_mean"], "under_share": u["under_share"], "rr": r["rr"], "stop_L": by["L"], "stop_S": by["S"],
            "d1": cap["d1"], "d2": cap["d2"], "maxpos": cap["maxpos"], "k": float(k), "daily_out": s_out}


def boot(sa, sr, rng):
    """Парный бутстреп по суткам: одни и те же сутки у (a) и правила; Δ Шарпа и Δ Σ — 5 / 95 %-ные точки."""
    nd = len(sa)
    ds, dsum = np.empty(NBOOT), np.empty(NBOOT)
    for i in range(NBOOT):
        ix = rng.integers(0, nd, nd)
        ds[i] = sharpe(sr[ix]) - sharpe(sa[ix])
        dsum[i] = sr[ix].sum() - sa[ix].sum()
    return {"dsharpe": [float(np.percentile(ds, 5)), float(np.percentile(ds, 95))], "dsum": [float(np.percentile(dsum, 5)), float(np.percentile(dsum, 95))]}


def selftest():
    # sweep_w: две сделки веса 2 и 1 внахлёст, третья после; D1 = 250·3, D2 с реализованным P&L
    t_in = np.array([0, 10, 40]) * MIN_MS
    t_out = np.array([30, 20, 50]) * MIN_MS
    r = sweep_w(t_in, t_out, np.array([2.0, 1.0, 1.0]), np.array([1.0, 5.0, 0.0]))
    assert r["d1"] == MARGIN * 3 and r["maxpos"] == 2 and abs(r["d2"] - MARGIN * 3) < 1e-9, r
    # выход и вход в одну мс — выход первым
    r = sweep_w(np.array([0, 10]) * MIN_MS, np.array([10, 20]) * MIN_MS, np.array([1.0, 1.0]), np.array([0.0, 0.0]))
    assert r["maxpos"] == 1 and r["d1"] == MARGIN
    w = inv_weights({"L": 25.4, "S": 16.3})
    assert abs(sum(w.values()) - 2) < 1e-12 and abs(w["L"] * 25.4 - w["S"] * 16.3) < 1e-9
    # vol24: лента 3 суток, доходности ±0,001 по очереди (std = 0,001·√(n/(n−1)) ≈ 0,001), свеча минуты входа и позже — огромный скачок (в окно не входит); дыра в минутах — доходностей меньше
    import shutil
    import tempfile
    global KL_ROOTS
    root, old = tempfile.mkdtemp(), KL_ROOTS
    try:
        KL_ROOTS = [root]
        os.makedirs(os.path.join(root, "XUSDT"))
        base = int(dt.datetime(2026, 7, 10, tzinfo=dt.timezone.utc).timestamp() * 1000)
        t_in = base + 36 * 60 * MIN_MS + 30_000                      # 12:00:30 11.07 → окно 12:00 10.07 … 11:59 11.07 (свечи < 12:00)
        for dd in range(3):
            d0 = base + dd * DAY_MS
            ot = d0 + np.arange(1440) * MIN_MS
            idx = (ot - base) // MIN_MS
            cl = 100.0 * np.exp(np.cumsum(np.where(idx % 2 == 0, 0.001, -0.001)))
            cl = np.where(ot >= (t_in // MIN_MS) * MIN_MS, cl * 10.0, cl)   # с минуты входа — скачок
            pd.DataFrame({"open_time": ot, "close": cl}).to_csv(os.path.join(root, "XUSDT", dt.datetime.fromtimestamp(d0 / 1000, dt.timezone.utc).strftime("%Y-%m-%d") + ".csv"), index=False)
        v = vol24("XUSDT", np.array([t_in]))[0]
        assert abs(v - 0.001) < 2e-5, v
        v2 = vol24("XUSDT", np.array([base + 60 * MIN_MS]))[0]        # вход через час от начала ленты: окно короче 720 доходностей → NaN
        assert v2 != v2, v2
        assert np.isnan(vol24("NOPE", np.array([t_in]))[0])
    finally:
        KL_ROOTS = old
        shutil.rmtree(root, ignore_errors=True)


# ───────────────────────────── отчёт ─────────────────────────────

def main():
    global D0_MS
    code, outdir = sys.argv[1:3]
    selftest()
    os.makedirs(outdir, exist_ok=True)
    full = dict(t63.LINES)
    t63.LINES = {k: v for k, v in full.items() if k in NEED}
    raw = t63.load_lines(code)
    st, rr = t63.st, t63.rr
    D0 = st.D0
    NW = st.NDAYS
    D0_MS = int(dt.datetime.combine(D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
    T, ND = {}, {}
    for k, df in raw.items():
        df = df.copy()
        df["day_out"] = ((df.t_out - D0_MS) // DAY_MS).astype(int)
        df["hold_min"] = (df.t_out - df.t_in) / MIN_MS
        T[k] = df.sort_values(["t_out", "t_in", "sym"], kind="stable").reset_index(drop=True)
        ND[k] = max(NW, int(df.day_out.max()) + 1)
    vol = {k: add_vol(T[k]) for k in T}
    for k in T:
        print("%-7s сделок %4d, волатильность монеты есть у %4d, медиана %.3e" % (k, len(T[k]), int(np.isfinite(vol[k]).sum()), float(np.nanmedian(vol[k]))))
    rng = np.random.default_rng(SEED)
    out = {"pairs": {}, "weights": {}}
    md = []
    P = md.append
    P("# Таблицы TK-667 (сгенерировано `tools/p42/tk667_alloc.py`; 775 контрактов, 01.07–13.11, 500 $ при весе 1, после комиссий, без фандинга)\n")
    P("Правила — `P-42 §13.67` (PS-935), записаны до счёта. Все $ — при весах W_i = k · w_i: правило отмасштабировано так, чтобы депозит D1 (250 $ × максимум суммы весов открытых позиций) был равен D1 правила (a). "
      "Сутки по закрытию (просадка, зазоры, под водой, Шарп «закр.»); Шарп «вход» — по дню входа (так считают TK-662 / TK-664). Модель веса: P&L и комиссия пропорциональны размеру, проскальзывание от размера не зависит.\n")
    flags = {}
    for pk, (lk, sk) in PAIRS.items():
        nd = max(ND[lk], ND[sk])
        both, W, info = build_weights(lk, sk, T, nd, vol, rr)
        base_cap = sweep_w(both.t_in.to_numpy(np.int64), both.t_out.to_numpy(np.int64), W["a"], both.pnl.to_numpy())
        D1a = base_cap["d1"]
        M = {}
        for key, _ in RULES:
            cap_raw = sweep_w(both.t_in.to_numpy(np.int64), both.t_out.to_numpy(np.int64), W[key], both.pnl.to_numpy() * W[key])
            M[key] = metrics(both, W[key], D1a / cap_raw["d1"], nd, NW, rr)
            assert abs(M[key]["d1"] - D1a) < 1e-6 * D1a, ("нормировка D1", pk, key, M[key]["d1"], D1a)
        # сверка (a)
        a = M["a"]
        assert a["n"] == len(T[lk]) + len(T[sk]) and abs(a["sum"] - float(T[lk].pnl.sum() + T[sk].pnl.sum())) < 1e-6, ("Σ пары (a)", pk)
        if pk in CHECK:
            c = CHECK[pk]
            assert abs(a["sum"] - c["sum"]) < 0.006 and abs(a["sharpe_in"] - c["sharpe_in"]) < 0.006 and abs(a["d1"] - c["d1"]) < 1e-9 and a["gap_longest"] == c["gap"] and abs(a["maxdd"] - c["maxdd"]) < 0.006, ("сверка (a) с TK-663 / TK-664", pk, {x: a[x] for x in ("sum", "sharpe_in", "d1", "gap_longest", "maxdd")}, c)
        print("%s (a): n %d, Σ %+.2f, Шарп вход %.2f / закр. %.2f, D1 %.0f, D2 %.0f, зазор %d, просадка %.2f — сверка %s" % (pk, a["n"], a["sum"], a["sharpe_in"], a["sharpe_out"], a["d1"], a["d2"], a["gap_longest"], a["maxdd"], "ок" if pk in CHECK else "нет эталона"))
        bs = {key: boot(a["daily_out"], M[key]["daily_out"], rng) for key, _ in RULES if key != "a"}
        # кандидатный признак по заранее записанному чтению: Шарп (закр.) выше, просадка ниже, Σ не ниже
        beat = {key: bool(M[key]["sharpe_out"] > a["sharpe_out"] and M[key]["maxdd"] < a["maxdd"] and M[key]["sum"] >= a["sum"]) for key, _ in RULES if key != "a"}
        flags[pk] = beat
        label = "%s = %s + %s" % (pk, lk, sk)
        P("## %s\n" % label)
        P("Сделок %d (%s %d / %s %d). Сырые веса: (b) стоп %s %s $ (n %d), %s %s $ (n %d) → w %s %s / %s %s; (c) σ суток %s %s $, %s %s $ → w %s / %s; (d) v_ref %s, без волатильности %s сделок, обрезано (d) %s; (c-causal): первые %d суток 1:1, доля сделок в них %s.\n" % (
            a["n"], lk, len(T[lk]), sk, len(T[sk]), lk, num(info["stop"][lk]["mean_loss"], 1), info["stop"][lk]["n"], sk, num(info["stop"][sk]["mean_loss"], 1), info["stop"][sk]["n"], lk, num(info["w_b"][lk], 3), sk, num(info["w_b"][sk], 3),
            lk, num(info["sigma"][lk], 2), sk, num(info["sigma"][sk], 2), num(info["w_c"][lk], 3), num(info["w_c"][sk], 3), "%.3e" % info["vref"], pct(info["d_no_vol_share"], 1), pct(info["d_clipped_share"], 1), WARM, pct(info["cc_warm_share"], 1)))
        rows = []
        for key, nm in RULES:
            m = M[key]
            bsk = bs.get(key)
            rows.append([nm, num(m["sum"], 2, True), num(m["mean"], 3, True), pct(m["sum"] / m["d1"], 1), num(m["sharpe_out"], 2), num(m["sharpe_in"], 2), num(m["maxdd"], 2), num(m["sum_dd"], 2), m["gap_longest"], num(m["gap_mean"], 1), pct(m["under_share"]),
                         num(m["rr"], 2), "%s / %s" % (num(m["stop_L"]["mean"], 1, True), num(m["stop_S"]["mean"], 1, True)), num(m["d1"], 0), num(m["d2"], 0), num(m["k"], 3),
                         "%s…%s / %s…%s" % (num(bsk["dsharpe"][0], 2, True), num(bsk["dsharpe"][1], 2, True), num(bsk["dsum"][0], 0, True), num(bsk["dsum"][1], 0, True)) if bsk else "—", ("да" if beat[key] else "нет") if bsk else "—"])
        P("\n".join(table(["правило", "Σ $", "на сделку $", "Σ / D1", "Шарп закр.", "Шарп вход", "макс. просадка $", "Σ / просадка", "самый длинный зазор, сут.", "средний зазор, сут.", "под водой", "R:R", "убыток на стоп $, лонг / шорт", "D1 $", "D2 $", "k", "90 % Δ Шарпа / Δ Σ $ против (a)", "Шарп↑, просадка↓, Σ≥ (a)"], rows)))
        out["pairs"][pk] = {"lines": [lk, sk], "info": info, "rules": {key: {x: v for x, v in M[key].items() if x != "daily_out"} for key, _ in RULES}, "boot": bs, "beat": beat}
        days = [(D0 + dt.timedelta(days=d)).isoformat() for d in range(nd)]
        pd.DataFrame({"date": days, **{"pnl_%s" % key: M[key]["daily_out"].round(4) for key, _ in RULES}}).to_csv(os.path.join(outdir, "perday-%s.csv" % pk), index=False, encoding="utf-8")
        tr = both[["sym", "side", "t_in", "t_out", "pnl", "kind"]].copy()
        for key, _ in RULES:
            tr["w_%s" % key] = W[key].round(5)
        tr.to_csv(os.path.join(outdir, "trades-%s.csv" % pk), index=False, encoding="utf-8")
    P("## Чтение по заранее записанному правилу (§13.67)\n")
    cand = {}
    for key, nm in (("b", "(b) равный стоп"), ("c", "(c) 1/σ суток"), ("d", "(d) 1/вол. монеты")):
        need = ("c", "cc") if key == "c" else (key,)
        npairs = {x: sum(flags[pk][x] for pk in PAIRS) for x in need}
        cand[key] = {"npairs": npairs, "candidate": all(v >= 2 for v in npairs.values())}
        P("- %s: %s из 3 пар при равном D1 — Шарп закр. выше, просадка ниже, Σ не ниже, чем у (a)%s → **%s**" % (
            nm, ", ".join("%s: %d" % (x, v) for x, v in npairs.items()), " (оба варианта должны пройти)" if key == "c" else "", "кандидат на проверку вперёд после 30.11 (решение координатора)" if cand[key]["candidate"] else "не выровняло / не дало денег на этих данных"))
    P("")
    out["candidates"] = cand
    open(os.path.join(outdir, "tables.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    json.dump(out, open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("\n".join(md[-6:]))


if __name__ == "__main__":
    main()
