#!/usr/bin/env python3
"""TK-667, добавка по просьбе критика (банк гипотез r2 `9ed24b67c`, строки 52 / 54 / 53): правила (h) хедж лонгов коротким BTC по бете и (n) делитель риска 1/c_i по одновременным позициям.
Правила записаны ДО чтения таблиц основного счёта: P-42 §13.67.1 (`PS-937`, `docs/findings/p42-allocation-775-2026-11-21/prereg-amend1.md`). Те же сделки, пары, нормировка на D1(a), метрики и чтение, что у tk667_alloc.py (импорт).
Строка 53 (корреляции монет) слита в 54 — отдельно не считается.
Запуск на srv: /opt/venv/bin/python tk667_extra.py <код TK-639 (/data/p42-tk639/code)> <каталог выхода>
Выход: tables-extra.md, summary-extra.json, perday-extra-<пара>.csv, beta-<пара>.csv (бета по лонгам). Только описание, вердикта нет."""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk667_alloc as A  # noqa: E402

num, pct, table, sharpe = A.num, A.pct, A.table, A.sharpe
HOUR_MS = 3_600_000
BETA_H = 168
BETA_NEED = 100
BETA_CLIP = (0.0, 3.0)
FEE = 3.5e-4 * 0.9                      # тейкер 3,5 bps минус 10 % возврата, на сторону
BTC_ROOTS = ["/data/p42-tk596/jul/klines/BTCUSDT", "/data/p42-tk596/aug/klines/BTCUSDT", "/data/klines/binance/BTCUSDT"]


def read_1m(sym, d_lo, d_hi):
    """1m свечи символа за сутки d_lo…d_hi (даты), столбцы open_time, open, close; пусто — None."""
    parts = []
    d = d_lo
    while d <= d_hi:
        p = A.kline_path(sym, d)
        if p:
            parts.append(pd.read_csv(p, usecols=["open_time", "open", "close"]))
        d += dt.timedelta(days=1)
    if not parts:
        return None
    k = pd.concat(parts).drop_duplicates("open_time").sort_values("open_time")
    return k.open_time.to_numpy(np.int64), k.open.to_numpy(float), k.close.to_numpy(float)


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def hourly_close(ot, cl, h0, h1):
    """close последней минуты каждого часа h0…h1 (час = мс // 3 600 000); нет свечей часа — NaN. Индекс = h − h0."""
    out = np.full(h1 - h0 + 1, np.nan)
    h = ot // HOUR_MS
    m = (h >= h0) & (h <= h1)
    hh, cc = h[m], cl[m]
    last = np.concatenate([hh[1:] != hh[:-1], [True]]) if len(hh) else np.array([], bool)
    out[hh[last] - h0] = cc[last]
    return out


def betas(df, btc_ot, btc_cl):
    """Причинная бета каждой сделки df (по t_in) на BTC: OLS-наклон часовых лог-доходностей за BETA_H часов строго до часа входа. NaN — мало данных."""
    out = np.full(len(df), np.nan)
    t_in = df.t_in.to_numpy(np.int64)
    for s, ix in df.groupby("sym").indices.items():
        H = t_in[ix] // HOUR_MS
        h0, h1 = int(H.min()) - BETA_H - 1, int(H.max()) - 1
        k = read_1m(s, day_of(h0 * HOUR_MS), day_of((h1 + 1) * HOUR_MS))
        if k is None:
            continue
        cs = hourly_close(k[0], k[2], h0, h1)
        cb = hourly_close(btc_ot, btc_cl, h0, h1)
        rs, rb = np.log(cs[1:] / cs[:-1]), np.log(cb[1:] / cb[:-1])      # rs[j] — час h0 + 1 + j
        for j, hj in zip(ix, H):
            a, b = int(hj) - BETA_H - (h0 + 1), int(hj) - (h0 + 1)       # часы hj − BETA_H … hj − 1
            x, y = rb[a:b], rs[a:b]
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() >= BETA_NEED:
                x, y = x[ok], y[ok]
                v = float(np.var(x, ddof=1))
                if v > 0:
                    out[j] = float(np.cov(x, y, ddof=1)[0, 1] / v)
    return out


def px_at(ot, op, t):
    """open минуты, содержащей t (мс); нет минуты — NaN."""
    m = (t // A.MIN_MS) * A.MIN_MS
    i = np.searchsorted(ot, m)
    ok = (i < len(ot)) & (ot[np.minimum(i, len(ot) - 1)] == m)
    return np.where(ok, op[np.minimum(i, len(ot) - 1)], np.nan)


def conc_at_entry(both):
    """c_i: позиций пары, открытых в момент входа i, считая i и вошедших раньше по порядку (t_in, t_out, sym), t_out_j > t_in_i."""
    o = np.lexsort((both.sym.to_numpy(), both.t_out.to_numpy(), both.t_in.to_numpy()))
    ti, to = both.t_in.to_numpy(np.int64)[o], both.t_out.to_numpy(np.int64)[o]
    c = np.zeros(len(o), np.int64)
    heap = []
    import heapq
    for r in range(len(o)):
        while heap and heap[0] <= ti[r]:
            heapq.heappop(heap)
        heapq.heappush(heap, to[r])
        c[r] = len(heap)
    out = np.zeros(len(o), np.int64)
    out[o] = c
    return out


def selftest():
    both = pd.DataFrame({"sym": ["A", "B", "C", "D"], "t_in": [0, 5, 5, 40], "t_out": [30, 10, 50, 60]})
    c = conc_at_entry(both)       # A:1; B (вход 5, A открыт):2; C (вход 5, A и B открыты, C после B по sym):3; D (вход 40, открыты C и сам D; A закрыт в 30):2
    assert c.tolist() == [1, 2, 3, 2], c
    ot = np.array([0, 60_000, 120_000])
    assert px_at(ot, np.array([1.0, 2.0, 3.0]), np.array([61_000, 500_000])).tolist()[0] == 2.0 and np.isnan(px_at(ot, np.array([1.0, 2.0, 3.0]), np.array([500_000]))[0])
    # бета: монета = 1,5 × BTC → β = 1,5
    h = np.arange(0, 200)
    rb = np.sin(h * 0.7) * 0.01
    cb = 100 * np.exp(np.cumsum(rb))
    cs = 10 * np.exp(np.cumsum(1.5 * rb))
    ot = h * HOUR_MS + 59 * A.MIN_MS
    hc_b, hc_s = hourly_close(ot, cb, 0, 199), hourly_close(ot, cs, 0, 199)
    x, y = np.log(hc_b[1:] / hc_b[:-1]), np.log(hc_s[1:] / hc_s[:-1])
    assert abs(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1) - 1.5) < 1e-9
    A.selftest()


def main():
    code, outdir = sys.argv[1:3]
    selftest()
    os.makedirs(outdir, exist_ok=True)
    t63 = A.t63
    full = dict(t63.LINES)
    t63.LINES = {k: v for k, v in full.items() if k in A.NEED}
    raw = t63.load_lines(code)
    st, rr = t63.st, t63.rr
    D0 = st.D0
    NW = st.NDAYS
    A.D0_MS = int(dt.datetime.combine(D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
    T, ND = {}, {}
    for k, df in raw.items():
        df = df.copy()
        df["day_out"] = ((df.t_out - A.D0_MS) // A.DAY_MS).astype(int)
        T[k] = df.sort_values(["t_out", "t_in", "sym"], kind="stable").reset_index(drop=True)
        ND[k] = max(NW, int(df.day_out.max()) + 1)
    # BTC 1m: open / close (сутки с запасом BETA_H часов до 01.07)
    last_ms = max(int(T[k].t_out.max()) for k in T)
    b = read_1m("BTCUSDT", day_of(A.D0_MS - (BETA_H + 2) * HOUR_MS), day_of(last_ms + A.DAY_MS))
    btc_ot, btc_op, btc_cl = b
    rng = np.random.default_rng(A.SEED + 1)
    out = {"pairs": {}}
    md = []
    P = md.append
    P("# Таблицы TK-667, добавка (сгенерировано `tools/p42/tk667_extra.py`; P-42 §13.67.1 / PS-937; 775 контрактов, 01.07–13.11, 500 $, после комиссий)\n")
    P("(h) хедж лонгов коротким BTCUSDT на β_i · 500 $ (β — OLS часовых лог-доходностей за 168 ч до входа, обрезка [0; 3]), комиссия тейкер 3,5 bps минус 10 % возврата на сторону, фандинг хеджа не считается; маржа хеджа входит в D1. "
      "(n) w_i = 1 / c_i, c_i — позиций пары открыто на входе i, считая её. Нормировка на D1 правила (a) и метрики — как в основной таблице; сравнение — с (a) той же пары. Бутстреп — по суткам, веса и беты не пересчитываются.\n")
    cand = {"h": {}, "n": {}}
    for pk, (lk, sk) in A.PAIRS.items():
        nd = max(ND[lk], ND[sk])
        both = pd.concat([T[lk], T[sk]], ignore_index=True)
        n = len(both)
        isL = (both.side.to_numpy() == "L")
        beta = np.full(n, np.nan)
        beta[isL] = betas(both[isL].reset_index(drop=True), btc_ot, btc_cl)
        fb = isL & ~np.isfinite(beta)
        beta_used = np.where(isL, np.where(np.isfinite(beta), np.clip(beta, *BETA_CLIP), 1.0), 0.0)
        pin, pout = px_at(btc_ot, btc_op, both.t_in.to_numpy(np.int64)), px_at(btc_ot, btc_op, both.t_out.to_numpy(np.int64))
        okpx = np.isfinite(pin) & np.isfinite(pout)
        assert okpx[isL].all(), ("нет цены BTC на входе / выходе лонга", pk, int((~okpx[isL]).sum()))
        notional = beta_used * A.NOTIONAL
        hedge = np.where(isL & okpx, -notional * (np.where(okpx, pout / pin, 1.0) - 1.0) - 2 * FEE * notional, 0.0)
        t_in, t_out = both.t_in.to_numpy(np.int64), both.t_out.to_numpy(np.int64)
        ones = np.ones(n)
        D1a = A.sweep_w(t_in, t_out, ones, both.pnl.to_numpy())["d1"]
        rules = {}
        rules["a"] = A.metrics(both, ones, 1.0, nd, NW, rr)
        assert abs(rules["a"]["d1"] - D1a) < 1e-6
        # (h)
        wm = 1.0 + beta_used
        d1h = A.sweep_w(t_in, t_out, wm, both.pnl.to_numpy() + hedge)["d1"]
        rules["h"] = A.metrics(both, ones, D1a / d1h, nd, NW, rr, hedge=hedge, wm=wm)
        # (n)
        c = conc_at_entry(both)
        wn = 1.0 / c
        d1n = A.sweep_w(t_in, t_out, wn, both.pnl.to_numpy() * wn)["d1"]
        rules["n"] = A.metrics(both, wn, D1a / d1n, nd, NW, rr)
        for key in ("h", "n"):
            assert abs(rules[key]["d1"] - D1a) < 1e-6 * D1a, ("нормировка D1", pk, key)
        a = rules["a"]
        bs = {key: A.boot(a["daily_out"], rules[key]["daily_out"], rng) for key in ("h", "n")}
        beat = {key: bool(rules[key]["sharpe_out"] > a["sharpe_out"] and rules[key]["maxdd"] < a["maxdd"] and rules[key]["sum"] >= a["sum"]) for key in ("h", "n")}
        hsum = float(hedge.sum() * rules["h"]["k"])
        hs = beta_used[isL]
        P("## %s = %s + %s\n" % (pk, lk, sk))
        P("Лонгов %d, бета: медиана %s, 5–95 %% %s…%s, β по умолчанию 1 (мало данных) у %d, обрезано у %d; Σ хеджа отдельно (при k) %s $; максимум позиций %d (в (n): c_i до %d, средний вес %s).\n" % (
            int(isL.sum()), num(float(np.median(hs)), 2), num(float(np.percentile(hs, 5)), 2), num(float(np.percentile(hs, 95)), 2), int(fb.sum()),
            int((np.isfinite(beta[isL]) & ((beta[isL] < BETA_CLIP[0]) | (beta[isL] > BETA_CLIP[1]))).sum()), num(hsum, 2, True), a["maxpos"], int(c.max()), num(float(wn.mean()), 3)))
        rows = []
        for key, nm in (("a", "(a) 500 $"), ("h", "(h) хедж BTC по бете"), ("n", "(n) 1/c_i")):
            m = rules[key]
            bsk = bs.get(key)
            rows.append([nm, num(m["sum"], 2, True), num(m["mean"], 3, True), pct(m["sum"] / m["d1"], 1), num(m["sharpe_out"], 2), num(m["sharpe_in"], 2), num(m["maxdd"], 2), num(m["sum_dd"], 2), m["gap_longest"], num(m["gap_mean"], 1), pct(m["under_share"]),
                         num(m["rr"], 2), "%s / %s" % (num(m["stop_L"]["mean"], 1, True), num(m["stop_S"]["mean"], 1, True)), num(m["d1"], 0), num(m["d2"], 0), num(m["k"], 3),
                         "%s…%s / %s…%s" % (num(bsk["dsharpe"][0], 2, True), num(bsk["dsharpe"][1], 2, True), num(bsk["dsum"][0], 0, True), num(bsk["dsum"][1], 0, True)) if bsk else "—", ("да" if beat[key] else "нет") if bsk else "—"])
        P("\n".join(table(["правило", "Σ $", "на сделку $", "Σ / D1", "Шарп закр.", "Шарп вход", "макс. просадка $", "Σ / просадка", "самый длинный зазор, сут.", "средний зазор, сут.", "под водой", "R:R", "убыток на стоп $, лонг / шорт", "D1 $", "D2 $", "k", "90 % Δ Шарпа / Δ Σ $ против (a)", "Шарп↑, просадка↓, Σ≥ (a)"], rows)))
        out["pairs"][pk] = {"lines": [lk, sk], "beta": {"median": float(np.median(hs)), "p5": float(np.percentile(hs, 5)), "p95": float(np.percentile(hs, 95)), "fallback": int(fb.sum())}, "hedge_sum_at_k": hsum,
                            "c_max": int(c.max()), "w_n_mean": float(wn.mean()), "rules": {k: {x: v for x, v in rules[k].items() if x != "daily_out"} for k in rules}, "boot": bs, "beat": beat}
        for key in ("h", "n"):
            cand[key][pk] = beat[key]
        days = [(D0 + dt.timedelta(days=d)).isoformat() for d in range(nd)]
        pd.DataFrame({"date": days, **{"pnl_%s" % k: rules[k]["daily_out"].round(4) for k in rules}}).to_csv(os.path.join(outdir, "perday-extra-%s.csv" % pk), index=False, encoding="utf-8")
        pd.DataFrame({"sym": both.sym[isL], "t_in": both.t_in[isL], "t_out": both.t_out[isL], "beta": np.round(beta[isL], 4), "beta_used": np.round(beta_used[isL], 4), "hedge_usd": np.round(hedge[isL], 4)}).to_csv(os.path.join(outdir, "beta-%s.csv" % pk), index=False, encoding="utf-8")
    P("## Чтение по заранее записанному правилу (§13.67 / §13.67.1)\n")
    res = {}
    for key, nm in (("h", "(h) хедж BTC по бете"), ("n", "(n) 1/c_i")):
        npairs = sum(cand[key].values())
        res[key] = {"npairs": npairs, "candidate": npairs >= 2}
        P("- %s: %d из 3 пар при равном D1 — Шарп закр. выше, просадка ниже, Σ не ниже, чем у (a) → **%s**" % (nm, npairs, "кандидат на проверку вперёд после 30.11 (решение координатора)" if npairs >= 2 else "не выровняло / не дало денег на этих данных"))
    P("")
    out["candidates"] = res
    open(os.path.join(outdir, "tables-extra.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    json.dump(out, open(os.path.join(outdir, "summary-extra.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("\n".join(md[-4:]))


if __name__ == "__main__":
    main()
