"""Взгляд на одну монету пары Bybit (A) – OKX (B) по событиям книг (мс): исполнимые Sell/Buy, уровень, спреды, круги против комиссий.

Запуск: python3 pair_look.py <каталог книг parsed/ (несколько — через «:», дубли записей снимаются)> <t0_мс> <t1_мс> <выход.json> <выход_ряда.npz> [A-символ B-символ [t1_вкладки_мс]]
Переменная KRX=1 — добавить режимы торгов KRX и окрестность закрытия (только для акций SK hynix; даты зашиты под окно 13–15.11.2026)
Sell = bid_A / ask_B − 1, Buy = ask_A / bid_B − 1 (viking xarb_base §2), в bps; время — E биржи; уровень c — 60-мин центр вкладки (core.rolling_centre).
В разобранных книгах (parse_book / parse_inc) запись есть только при смене пары (bid, ask) (повторы цены сняты при разборе, кроме опоздавших > 2 с): размер на лучшем уровне — на момент смены цены, изменения одного размера между сменами цены не записаны. Здесь снимаются только пустые и пересечённые записи.
Два способа счёта кругов против комиссий F (bps) на исполнимых ценах:
  (I) как на вкладке: вход — Sell − c ≥ F (или c − Buy ≥ F), выход — середина вернулась к c; чистая прибыль = Sell_вход − Buy_выход − F;
  (II) наилучший круг задним числом: зигзаг по Sell (верхушки) и Buy (впадины) с размахом ≥ F; вход и выход — экстремумы, цена исполнима.
Устойчивость — через L мс (50 / 300 / 10000): условие по последнему событию ≤ t + L и непрерывное удержание.
"""
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402

FEES = {"taker4": 12.6, "mt_1m3t": 10.71, "mt_2m2t": 8.82}   # bps: 4 тейкерских ноги по 3,15; 1 мейкер (1,26) + 3 тейкера; 2 + 2
LS = (50, 300, 10000)
CAP = 60_000
BLK = 3_600_000


def load(work, ex, sym, unit):
    fs = sorted(f for d in work.split(":") for f in glob.glob(str(Path(d, ex, sym + ".*.npz"))))
    parts = [dict(np.load(f)) for f in fs]
    a = {k: np.concatenate([p[k] for p in parts]) for k in ("e", "rx", "bid", "ask", "bsz", "asz")}
    o = np.lexsort((a["asz"], a["bsz"], a["ask"], a["bid"], a["rx"], a["e"]))
    a = {k: v[o] for k, v in a.items()}
    n_raw = len(a["e"])
    dup = np.r_[False, np.all([a[k][1:] == a[k][:-1] for k in ("e", "rx", "bid", "ask", "bsz", "asz")], axis=0)]   # одна запись из двух разборов с перекрытием
    a = {k: v[~dup] for k, v in a.items()}
    ok = (a["bid"] > 0) & (a["ask"] > 0) & (a["bid"] <= a["ask"])
    a = {k: v[ok] for k, v in a.items()}
    a["dbid"], a["dask"] = a["bsz"] * a["bid"] * unit, a["asz"] * a["ask"] * unit
    return a, {"files": len(fs), "raw": n_raw, "dup": int(dup.sum()), "bad": int((~ok).sum())}


def tick_of(a):
    p = np.unique(np.concatenate([a["bid"], a["ask"]]))
    d = np.diff(p)
    return float(np.round(d[d > 1e-9].min(), 8))


def wq(x, w, qs):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    xs = x[o]
    return [float(xs[min(int(np.searchsorted(c, q * c[-1])), len(xs) - 1)]) for q in qs]


def wmean(x, w):
    return float(np.average(x, weights=w)) if w.sum() > 0 else float("nan")


def run_end(ok, T, k0, t_end):
    """Непрерывный отрезок истинного ok (булев ряд по событиям, ok[k0] истинно): (время начала, время конца); конец — первое событие, где ok ложно."""
    bad = np.flatnonzero(~ok)
    q = int(np.searchsorted(bad, k0))
    a = int(bad[q - 1]) + 1 if q > 0 else 0
    return int(T[a]), int(T[bad[q]]) if q < len(bad) else int(t_end)


def episodes_centre(T, S, Bu, c, dS, dBu, t0, t1, F):
    """(I): эпизоды вкладки при пороге F; строки-словари. T ≥ t0 и центр известен."""
    v = (T >= t0) & ~np.isnan(c)
    Tv, Sv, Bv, cv, dSv, dBv = T[v], S[v], Bu[v], c[v], dS[v], dBu[v]
    Es, Eb, Mv = Sv - cv, cv - Bv, (Sv + Bv) / 2
    rows = []
    for dirn, ent, ex, e in ((1, Es >= F, Mv <= cv, Es), (-1, Eb >= F, Mv >= cv, Eb)):
        below = np.flatnonzero(e < F)
        for i, j in core.episodes(Tv, ent, ex):
            cens = j < 0
            jj = len(Tv) - 1 if cens else j
            r = {"t": int(Tv[i]), "dir": dirn, "cens": cens, "tau_ms": int((t1 if cens else Tv[j]) - Tv[i]), "e0": float(e[i]), "peak": float(e[i:jj + 1].max())}
            q = int(np.searchsorted(below, i, "right"))
            r["life_ms"] = int((Tv[below[q]] if q < len(below) else t1) - Tv[i])
            if dirn == 1:
                r["din"], r["dout"] = float(dSv[i]), (float(dBv[j]) if not cens else None)
                r["gross"] = float(Sv[i] - Bv[j]) if not cens else None
            else:
                r["din"], r["dout"] = float(dBv[i]), (float(dSv[j]) if not cens else None)
                r["gross"] = float(Sv[j] - Bv[i]) if not cens else None
            for L in LS:
                k = int(np.searchsorted(Tv, Tv[i] + L, "right")) - 1
                r[f"st{L}"] = bool(e[k] >= F)
                if cens or not (Tv[i] + L < Tv[j]):
                    r[f"rt{L}"] = False
                else:
                    r[f"rt{L}"] = bool((Sv[k] - Bv[j] if dirn == 1 else Sv[j] - Bv[k]) >= F)
            rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def enrich(rows, T, tA, tB, midA, midB):
    """К эпизодам (I): какая биржа дала событие входа и на сколько bps сдвинулась середина каждой биржи за 1 с до входа."""
    for r in rows:
        i = int(np.searchsorted(T, r["t"]))
        j = max(int(np.searchsorted(T, r["t"] - 1000, "right")) - 1, 0)
        r["evA"] = bool(np.searchsorted(tA, r["t"]) < len(tA) and tA[np.searchsorted(tA, r["t"])] == r["t"])
        r["evB"] = bool(np.searchsorted(tB, r["t"]) < len(tB) and tB[np.searchsorted(tB, r["t"])] == r["t"])
        r["dA_bps"] = float((midA[i] / midA[j] - 1) * 1e4)
        r["dB_bps"] = float((midB[i] / midB[j] - 1) * 1e4)
    return rows


def zigzag(T, S, Bu, dS, dBu, t0, t1, F):
    """(II): круги задним числом. -> список словарей; круг учитывается, когда подтверждён следующий разворот."""
    n = len(T)
    Sl, Bl = S.tolist(), Bu.tolist()
    mode, hi, ihi, lo, ilo = 0, Sl[0], 0, Bl[0], 0
    out = []
    for k in range(1, n):
        s, b = Sl[k], Bl[k]
        if mode == 0:
            if s > hi:
                hi, ihi = s, k
            if b < lo:
                lo, ilo = b, k
            if hi - b >= F:
                mode, lo, ilo = 1, b, k
            elif s - lo >= F:
                mode, hi, ihi = 2, s, k
        elif mode == 1:
            if b < lo:
                lo, ilo = b, k
            elif s - lo >= F:
                out.append((1, ihi, ilo))
                mode, hi, ihi = 2, s, k
        else:
            if s > hi:
                hi, ihi = s, k
            elif hi - b >= F:
                out.append((2, ilo, ihi))
                mode, lo, ilo = 1, b, k
    rows = []
    for kind, ie, ix in out:
        # kind 1: вход Sell (ie — верхушка), выход Buy (ix — впадина); kind 2: вход Buy (ie — впадина), выход Sell (ix — верхушка)
        if T[ie] < t0 or T[ix] >= t1:
            continue
        if kind == 1:
            gross = S[ie] - Bu[ix]
            ok_in, ok_out = S >= Bu[ix] + F, Bu <= S[ie] - F      # вход ещё окупается при выходе в экстремуме; выход ещё окупается при входе в экстремуме
            din, dout = dS[ie], dBu[ix]
        else:
            gross = S[ix] - Bu[ie]
            ok_in, ok_out = Bu <= S[ix] - F, S >= Bu[ie] + F
            din, dout = dBu[ie], dS[ix]
        si, ei = run_end(ok_in, T, ie, t1)
        so, eo = run_end(ok_out, T, ix, t1)
        r = {"t": int(T[ie]), "t_out": int(T[ix]), "kind": kind, "gross": float(gross), "hold_ms": int(T[ix] - T[ie]), "life_in_ms": ei - si, "life_out_ms": eo - so,
             "life_ms": min(ei - si, eo - so), "din": float(din), "dout": float(dout)}
        for L in LS:
            r[f"hold{L}"] = r["life_ms"] >= L
        rows.append(r)
    return rows


def summarize_I(rows, F, days):
    if not rows:
        return {"n": 0}
    ex = [r for r in rows if not r["cens"]]
    net = np.array([r["gross"] - F for r in ex])
    dep = np.array([min(r["din"], r["dout"]) for r in ex])
    q = lambda a, qs: [float(np.percentile(a, p)) for p in qs] if len(a) else None  # noqa: E731
    out = {"n": len(rows), "cens": len(rows) - len(ex), "per_day": len(rows) / days,
           "n_dir1": sum(r["dir"] == 1 for r in rows), "n_dirm1": sum(r["dir"] == -1 for r in rows),
           "e0": q([r["e0"] for r in rows], (50, 95, 100)), "peak": q([r["peak"] for r in rows], (50, 95, 100)),
           "tau_ms": q([r["tau_ms"] for r in ex], (50, 90, 100)), "life_ms": q([r["life_ms"] for r in rows], (50, 90, 100)),
           "gross_ge_F": int((net >= 0).sum()), "net": q(net, (50, 95)), "net_sum_bps": float(net.sum()) if len(net) else 0.0,
           "din": q([r["din"] for r in rows], (10, 50, 90)), "dout": q([r["dout"] for r in ex], (10, 50, 90)),
           "depth_min": q(dep, (0, 50, 100)),
           "net_usd_at_depth": [float(x) for x in (np.sum(np.maximum(net, 0) * dep) / 1e4, np.sum(net * dep) / 1e4)] if len(net) else None}
    for L in LS:
        out[f"state{L}"] = int(sum(r[f"st{L}"] for r in rows))
        out[f"clear{L}"] = int(sum(r[f"rt{L}"] for r in rows))
        out[f"life_ge{L}"] = int(sum(r["life_ms"] >= L for r in rows))
    return out


def summarize_II(rows, F, days):
    if not rows:
        return {"n": 0}
    q = lambda a, qs: [float(np.percentile(a, p)) for p in qs]  # noqa: E731
    dep = np.array([min(r["din"], r["dout"]) for r in rows])
    gr = np.array([r["gross"] for r in rows])
    out = {"n": len(rows), "per_day": len(rows) / days, "n_kind1": sum(r["kind"] == 1 for r in rows), "n_kind2": sum(r["kind"] == 2 for r in rows),
           "gross": q(gr, (50, 95, 100)), "net": q(gr - F, (50, 95)), "net_sum_bps": float((gr - F).sum()),
           "hold_ms": q([r["hold_ms"] for r in rows], (10, 50, 90)), "life_ms": q([r["life_ms"] for r in rows], (10, 50, 90)),
           "life_in_ms": q([r["life_in_ms"] for r in rows], (10, 50, 90)), "life_out_ms": q([r["life_out_ms"] for r in rows], (10, 50, 90)),
           "depth_min": q(dep, (10, 50, 90)), "net_usd_at_depth": float(np.sum((gr - F) * dep) / 1e4)}
    for L in LS:
        out[f"hold{L}"] = int(sum(r[f"hold{L}"] for r in rows))
    return out


def main(work, t0, t1, out_json, out_npz, symA="SKHYNIXUSDT", symB="SKHYNIX-USDT-SWAP", t1_tab=None):
    res = {"t0": t0, "t1": t1, "symA": symA, "symB": symB, "fees_bps": FEES}
    unitB = 1.0
    for day in ("2026-11-14", "2026-11-13"):
        p = Path("/data/collect/okx/data", f"pool-{day}.json")
        m = (json.load(open(p)).get("meta") or {}).get(symB)
        if m:
            res["okx_meta"], unitB = m, float(m.get("ctVal") or 1.0)
            break
    for day in ("2026-11-14", "2026-11-13"):
        p = Path("/data/turbo-probe/soak-bybit/data", f"pool-{day}.json")
        if p.exists():
            m = (json.load(open(p)).get("meta") or {}).get(symA)
            if m:
                res["bybit_meta"] = m
                break
    A, ia_ = load(work, "bybit", symA, 1.0)
    B, ib_ = load(work, "okx", symB, unitB)
    res["load"] = {"bybit": ia_, "okx": ib_}
    tkA, tkB = tick_of(A), float(res.get("okx_meta", {}).get("tickSz") or tick_of(B))
    res["tick"] = {"bybit": tkA, "okx": tkB, "okx_data": tick_of(B)}
    tA, tB = A["e"], B["e"]
    T = np.union1d(tA, tB)
    ia = np.searchsorted(tA, T, "right") - 1
    ib = np.searchsorted(tB, T, "right") - 1
    ok = (ia >= 0) & (ib >= 0) & (T < t1)
    T, ia, ib = T[ok], ia[ok], ib[ok]
    S = (A["bid"][ia] / B["ask"][ib] - 1.0) * 1e4
    Bu = (A["ask"][ia] / B["bid"][ib] - 1.0) * 1e4
    dS = np.minimum(A["dbid"][ia], B["dask"][ib])     # $ на лучших уровнях для входа/выхода по Sell: продать A по биду, купить B по оферу
    dBu = np.minimum(A["dask"][ia], B["dbid"][ib])
    spA, spB = (A["ask"][ia] - A["bid"][ia]) / tkA, (B["ask"][ib] - B["bid"][ib]) / tkB
    midA, midB = (A["bid"][ia] + A["ask"][ia]) / 2, (B["bid"][ib] + B["ask"][ib]) / 2
    res["n_pair_events"] = int(len(T))
    res["n_pair_events_window"] = int((T >= t0).sum())
    res["first_T"], res["last_T"] = int(T[0]), int(T[-1])
    # центр вкладки
    mins, nb, sb, bb = core.minute_bars(T, S, Bu)
    m0, cen = core.rolling_centre(mins, sb, bb)
    c = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    res["centre_first_valid"] = int(T[np.flatnonzero(~np.isnan(c))[0]])
    dt = np.minimum(np.diff(T, append=t1), CAP).astype(float)
    M, H = (S + Bu) / 2, (Bu - S) / 2
    qs = (0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999)
    w = T >= t0
    res["window_h"] = float((t1 - t0) / 3.6e6)
    res["cover_s"] = float(dt[w].sum() / 1000)
    # события и глубина
    res["events"] = {"bybit": int(((tA >= t0) & (tA < t1)).sum()), "okx": int(((tB >= t0) & (tB < t1)).sum())}
    res["gap_max_s"] = {"bybit": float(np.diff(tA[(tA >= t0) & (tA < t1)]).max() / 1000), "okx": float(np.diff(tB[(tB >= t0) & (tB < t1)]).max() / 1000)}
    res["series"] = {"S_q": wq(S[w], dt[w], qs), "Bu_q": wq(Bu[w], dt[w], qs), "M_q": wq(M[w], dt[w], qs), "H_q": wq(H[w], dt[w], (0.05, 0.5, 0.95)),
                     "S_min": float(S[w].min()), "S_max": float(S[w].max()), "Bu_min": float(Bu[w].min()), "Bu_max": float(Bu[w].max()), "qs": qs,
                     "S_gt0_share": float(dt[w & (S > 0)].sum() / dt[w].sum()), "Bu_lt0_share": float(dt[w & (Bu < 0)].sum() / dt[w].sum()),
                     "centre_mean": wmean(c[w & ~np.isnan(c)], dt[w & ~np.isnan(c)])}
    sa, sb_ = spA[w], spB[w]
    res["spread_ticks"] = {"bybit": {"q": wq(sa, dt[w], (0.1, 0.5, 0.9, 0.99)), "share_1": float(dt[w][sa <= 1.5].sum() / dt[w].sum()), "share_le2": float(dt[w][sa <= 2.5].sum() / dt[w].sum()),
                                     "bps_med": wq(sa * tkA / np.median(A["bid"]) * 1e4, dt[w], (0.5,))[0]},
                           "okx": {"q": wq(sb_, dt[w], (0.1, 0.5, 0.9, 0.99)), "share_1": float(dt[w][sb_ <= 1.5].sum() / dt[w].sum()), "share_le2": float(dt[w][sb_ <= 2.5].sum() / dt[w].sum()),
                                   "bps_med": wq(sb_ * tkB / np.median(B["bid"]) * 1e4, dt[w], (0.5,))[0]}}
    dwA = np.minimum(np.diff(tA, append=t1), CAP).astype(float)
    dwB = np.minimum(np.diff(tB, append=t1), CAP).astype(float)
    kA, kB = (tA >= t0) & (tA < t1), (tB >= t0) & (tB < t1)
    res["depth_usd"] = {"bybit_bid": wq(A["dbid"][kA], dwA[kA], (0.1, 0.5, 0.9)), "bybit_ask": wq(A["dask"][kA], dwA[kA], (0.1, 0.5, 0.9)),
                        "okx_bid": wq(B["dbid"][kB], dwB[kB], (0.1, 0.5, 0.9)), "okx_ask": wq(B["dask"][kB], dwB[kB], (0.1, 0.5, 0.9)),
                        "pair_sell": wq(dS[w], dt[w], (0.1, 0.5, 0.9)), "pair_buy": wq(dBu[w], dt[w], (0.1, 0.5, 0.9))}
    # часовые строки (UTC) по всему разобранному ряду
    h0 = (int(T[0]) // BLK) * BLK
    hours = []
    for hs in range(h0, int(t1), BLK):
        m = (T >= hs) & (T < hs + BLK)
        if m.sum() < 2:
            continue
        d_ = dt[m]
        okc = m & ~np.isnan(c)
        hours.append({"h": int(hs), "nA": int(((tA >= hs) & (tA < hs + BLK)).sum()), "nB": int(((tB >= hs) & (tB < hs + BLK)).sum()),
                      "M50": wq(M[m], d_, (0.5,))[0], "S50": wq(S[m], d_, (0.5,))[0], "Bu50": wq(Bu[m], d_, (0.5,))[0], "S99": wq(S[m], d_, (0.99,))[0], "Bu01": wq(Bu[m], d_, (0.01,))[0],
                      "Smax": float(S[m].max()), "Bumin": float(Bu[m].min()), "H50": wq(H[m], d_, (0.5,))[0], "spA50": wq(spA[m], d_, (0.5,))[0], "spB50": wq(spB[m], d_, (0.5,))[0],
                      "c_end": float(c[okc][-1]) if okc.any() else None, "partial": bool(hs < t0 < hs + BLK),
                      "px50": wq(midA[m], d_, (0.5,))[0], "rng_pct": float((midA[m].max() - midA[m].min()) / np.median(midA[m]) * 100),
                      "dS50": wq(dS[m], d_, (0.5,))[0], "dBu50": wq(dBu[m], d_, (0.5,))[0]})
    res["hours"] = hours
    # эпизоды: считаются от первого события с известным центром / от начала ряда, в итог окна идут только с t ≥ t0
    days = (t1 - t0) / 86_400_000
    t_v = res["centre_first_valid"]
    res["I"], res["II"], eps_all = {}, {}, {}
    for name, F in FEES.items():
        rows = enrich(episodes_centre(T, S, Bu, c, dS, dBu, t_v, t1, F), T, tA, tB, midA, midB)
        eps_all[name] = rows
        res["I"][name] = summarize_I([r for r in rows if r["t"] >= t0], F, days)
        zz = zigzag(T, S, Bu, dS, dBu, int(T[0]), t1, F)
        eps_all[name + "_II"] = zz
        res["II"][name] = summarize_II([r for r in zz if r["t"] >= t0], F, days)
    fri_close = 1794551400000   # 13.11 06:30 UTC
    if os.environ.get("KRX"):
        # режимы (UTC): торги KRX (пн–пт 00:00–06:30 UTC; в ряду есть только пятница 13.11 с 04:44), после закрытия в будни, выходные
        sat0 = 1794614400000   # 14.11 00:00 UTC
        reg = {"krx_open_fri": (int(T[0]), fri_close), "fri_after_close": (fri_close, sat0), "weekend": (sat0, int(t1))}
        res["regimes"] = {}
        for rn, (a_, b_) in reg.items():
            m = (T >= a_) & (T < b_)
            d_ = dt[m]
            res["regimes"][rn] = {
                "from": a_, "to": b_, "h": float((b_ - a_) / 3.6e6), "nA": int(((tA >= a_) & (tA < b_)).sum()), "nB": int(((tB >= a_) & (tB < b_)).sum()),
                "M_q": wq(M[m], d_, (0.05, 0.5, 0.95)), "S_q": wq(S[m], d_, (0.01, 0.5, 0.99)), "Bu_q": wq(Bu[m], d_, (0.01, 0.5, 0.99)),
                "Smax": float(S[m].max()), "Bumin": float(Bu[m].min()), "H_med": wq(H[m], d_, (0.5,))[0],
                "spA": wq(spA[m], d_, (0.5, 0.9)), "spB": wq(spB[m], d_, (0.5, 0.9)), "spA_share1": float(d_[spA[m] <= 1.5].sum() / d_.sum()),
                "dS_med": wq(dS[m], d_, (0.5,))[0], "dBu_med": wq(dBu[m], d_, (0.5,))[0],
                "I": {n: int(sum(a_ <= r["t"] < b_ for r in eps_all[n])) for n in FEES}, "II": {n: int(sum(a_ <= r["t"] < b_ for r in eps_all[n + "_II"])) for n in FEES}}
    # блоки по 6 ч от границ суток GMT+4 (20:00, 02:00, 08:00, 14:00 UTC), обрезаны окном [t0, t1)
    b6 = []
    for bs in range((t0 - 20 * BLK) // (6 * BLK) * (6 * BLK) + 20 * BLK, int(t1), 6 * BLK):
        a_, b_ = max(bs, t0), min(bs + 6 * BLK, t1)
        m = (T >= a_) & (T < b_)
        if m.sum() < 2:
            continue
        d_ = dt[m]
        b6.append({"from": int(a_), "to": int(b_), "nA": int(((tA >= a_) & (tA < b_)).sum()), "nB": int(((tB >= a_) & (tB < b_)).sum()), "M_q": wq(M[m], d_, (0.05, 0.5, 0.95)),
                   "S50": wq(S[m], d_, (0.5,))[0], "Bu50": wq(Bu[m], d_, (0.5,))[0], "Smax": float(S[m].max()), "Bumin": float(Bu[m].min()), "H50": wq(H[m], d_, (0.5,))[0],
                   "spA50": wq(spA[m], d_, (0.5,))[0], "spB50": wq(spB[m], d_, (0.5,))[0], "c_mean": wmean(c[m & ~np.isnan(c)], dt[m & ~np.isnan(c)]),
                   "I": {n: int(sum(a_ <= r["t"] < b_ for r in eps_all[n])) for n in FEES}, "II": {n: int(sum(a_ <= r["t"] < b_ for r in eps_all[n + "_II"])) for n in FEES}})
    res["blocks6"] = b6
    if os.environ.get("KRX"):
        # окрестность закрытия KRX 13.11 06:30 UTC: корзины по 10 мин, 05:30–07:30 UTC
        cl = []
        for hs in range(fri_close - 60 * 60_000, fri_close + 60 * 60_000, 600_000):
            m = (T >= hs) & (T < hs + 600_000)
            if m.sum() < 2:
                continue
            d_ = dt[m]
            cl.append({"t": hs, "nA": int(((tA >= hs) & (tA < hs + 600_000)).sum()), "nB": int(((tB >= hs) & (tB < hs + 600_000)).sum()), "M50": wq(M[m], d_, (0.5,))[0],
                       "S50": wq(S[m], d_, (0.5,))[0], "Bu50": wq(Bu[m], d_, (0.5,))[0], "Smax": float(S[m].max()), "Bumin": float(Bu[m].min()), "H50": wq(H[m], d_, (0.5,))[0],
                       "spA50": wq(spA[m], d_, (0.5,))[0], "spB50": wq(spB[m], d_, (0.5,))[0]})
        res["close10"] = cl
    # сверка со вкладкой: последние 24 ч до t1_tab
    if t1_tab:
        rows = episodes_centre(T, S, Bu, c, dS, dBu, t1_tab - 86_400_000, t1_tab, FEES["taker4"])
        rows = [r for r in rows if r["t"] < t1_tab]
        res["tab_check"] = {"t1_tab": t1_tab, "I_taker4": summarize_I(rows, FEES["taker4"], 1.0), "ep": rows}
    json.dump(res, open(out_json, "w"), ensure_ascii=False, default=float)
    json.dump(eps_all, open(str(out_json) + ".eps", "w"), ensure_ascii=False, default=float)
    np.savez(out_npz, T=T, S=S.astype(np.float32), Bu=Bu.astype(np.float32), c=c.astype(np.float32), dS=dS.astype(np.float32), dBu=dBu.astype(np.float32),
             spA=spA.astype(np.float32), spB=spB.astype(np.float32))
    print("готово", len(T), "событий пары")


if __name__ == "__main__":
    a = sys.argv
    main(a[1], int(a[2]), int(a[3]), a[4], a[5], *(a[6:8] if len(a) >= 8 else ("SKHYNIXUSDT", "SKHYNIX-USDT-SWAP")), int(a[8]) if len(a) >= 9 else None)
