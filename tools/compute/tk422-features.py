#!/usr/bin/env python3
"""TK-422: счётчик признаков пампа/дампа по окну. Описание признаков — docs/findings/pump-features-2026-10-27.md.
Использование: tk422-features.py <окна.csv> <выход.csv> [--candles /srv/alpha-sda/candles-1m] [--tk415 /srv/alpha-sda/tk415/out]
               [--trades /data/tk417] [--grid grid.json] [--procs N]
Окна: колонки биржа|exchange, рынок|market, инструмент|монета|symbol, t_start, t_peak, t_end (ISO-минута UTC или мс), [знак|sign: памп/дамп/+1/-1].
Нет знака — по цене (конец пика против начала). Сырьё не сохраняется: свечи 1 м, OI/фандинг/ликвидации и сделки → строка на окно.
Все ценовые признаки считаются в «ориентации пампа»: для дампа цена берётся с минусом (high<->low), поэтому «вверх» = по ходу события.
Расстояния — bps от цены открытия минуты старта. Пустое значение = данных нет (не 0). Только ядро Python, без numpy."""
import csv, gzip, json, math, os, sys
from bisect import bisect_right
from datetime import datetime, timezone
from multiprocessing import Pool

GRID = {
    "H": [60, 240, 1440],          # глубина пула ликвидности назад, мин
    "K": [1, 2],                   # половина-ширина фрактала (1 = 3 свечи)
    "N": [5, 30],                  # возврат телом: за сколько минут после пика
    "T": [0, 10, 25],              # закрепление: на сколько bps закрытие за уровнем
    "R": [60, 180, 360],           # диапазон накопления AMD, мин
    "KM": [5, 15],                 # длина окна выноса после старта, мин
    "FVGMIN": [0, 5, 10],          # минимум размера FVG, bps
    "RET": [0.3, 0.5, 0.618, 0.786],  # уровни откатной ноги
    "IMB": [0.1, 0.2, 0.3],        # порог дисбаланса поглощения
    "SURGE": [2, 3, 5],            # порог объёма минуты к норме
    "ABS_N": [5, 15],              # окно у пика для поглощения, мин
    "DISP": [0, 10],               # допустимый сдвиг цены при поглощении, bps
}
BASE = 1440  # минут нормы


def tms(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    return int(datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def col(h, *names):
    for n in names:
        if n in h:
            return n
    return None


def fnum(x):
    return "" if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else (round(x, 6) if isinstance(x, float) else x)


def med(x):
    y = sorted(x)
    return y[len(y) // 2] if y else None


_cc = {}


def candles(root, ex, mk, sym):
    key = (ex, mk, sym)
    if key not in _cc:
        d = {}
        fn = "%s/%s-%s/%s.csv.gz" % (root, ex, mk, sym)
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    d[int(p[0]) // 60000] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[6]))
        _cc[key] = d
    return _cc[key]


def spot_name(ex, sym):
    return sym.replace("_", "") if ex == "bybit" else sym


def btc_name(ex):
    return "BTCUSDT" if ex == "bybit" else "BTC_USDT"


def fut_mkt(ex):
    return "linear" if ex == "bybit" else "futures_usdt"


def orient(d, s):
    """минута -> (o,h,l,c,q) в ориентации пампа"""
    if s > 0:
        return d
    return {m: (-v[0], -v[2], -v[1], -v[3], v[4]) for m, v in d.items()}


def seq(d, a, b):
    return [(m, d[m]) for m in range(a, b) if m in d]


def fractals(bars, k, hi=True):
    """индексы фракталов в списке bars [(m,(o,h,l,c,q))]; фрактал i подтверждён к бару i+k"""
    out = []
    j = 1 if hi else 2
    for i in range(k, len(bars) - k):
        v = bars[i][1][j]
        if hi:
            ok = all(bars[i][1][1] > bars[i + t][1][1] and bars[i][1][1] > bars[i - t][1][1] for t in range(1, k + 1))
        else:
            ok = all(bars[i][1][2] < bars[i + t][1][2] and bars[i][1][2] < bars[i - t][1][2] for t in range(1, k + 1))
        if ok:
            out.append(i)
    return out


def bps(a, b, p0):
    return (b - a) / p0 * 1e4


def ret_close(d, mp, level, N, T, p0):
    """после пика mp: есть ли закрытие ниже level на T bps в течение N минут"""
    for m in range(mp + 1, mp + N + 1):
        if m in d and d[m][3] <= level - T / 1e4 * p0:
            return 1
    return 0


def corr(x, y):
    n = len(x)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def rets(d, a, b):
    """доходности закрытий соседних минут (только если обе минуты есть) -> {m: ret}"""
    out = {}
    for m in range(a + 1, b):
        if m in d and (m - 1) in d and d[m - 1][3] != 0:
            out[m] = d[m][3] / d[m - 1][3] - 1
    return out


def pair_corr(r1, r2, lag=0):
    ks = [m for m in r1 if (m + lag) in r2]
    return corr([r1[m] for m in ks], [r2[m + lag] for m in ks]), len(ks)


def read_5m(fn, cols):
    """OI/фандинг/ликвидации: [(t_ms, {имя: значение})] по заголовку"""
    if not os.path.exists(fn):
        return None
    with gzip.open(fn, "rt") as f:
        h = next(f).rstrip("\n").split(",")
        ix = {c: h.index(c) for c in cols if c in h}
        out = []
        for line in f:
            p = line.rstrip("\n").split(",")
            out.append((int(p[0]), {c: float(p[i]) if i < len(p) and p[i] != "" else None for c, i in ix.items()}))
    return out


def at_or_before(arr, t):
    i = bisect_right([a[0] for a in arr], t) - 1
    return arr[i] if i >= 0 else None


def trades_min(root, sym, ma, mb):
    """Bybit linear: поминутно buy_usd, sell_usd из сделок /data/tk417/bybit-linear/<SYM>/<SYM>YYYY-MM-DD.csv.gz"""
    out = {}
    day0 = datetime.fromtimestamp(ma * 60, timezone.utc).date()
    day1 = datetime.fromtimestamp(mb * 60, timezone.utc).date()
    from datetime import timedelta
    d = day0
    found = False
    while d <= day1:
        fn = "%s/bybit-linear/%s/%s%s.csv.gz" % (root, sym, sym, d.isoformat())
        if os.path.exists(fn):
            found = True
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    m = int(float(p[0]) // 60)
                    if m < ma or m > mb:
                        continue
                    b = out.setdefault(m, [0.0, 0.0])
                    b[0 if p[2] == "Buy" else 1] += float(p[9])
        d += timedelta(days=1)
    return out if found else None


def features(w, roots, G):
    ex, mk, sym = w["ex"], w["mk"], w["sym"]
    croot, oroot, troot = roots
    F = {}
    d0 = candles(croot, ex, mk, sym)
    ms, mp, me = w["t0"] // 60000, w["tp"] // 60000, w["t1"] // 60000
    if ms not in d0 or mp not in d0 or me not in d0 or not (ms <= mp <= me):
        F["na"] = "нет свечи старта/пика/конца или порядок t_start<=t_peak<=t_end нарушен"
        return F
    s = w["s"] if w["s"] else (1 if d0[mp][1] >= d0[ms][0] else -1)
    F["sign"] = s
    D = orient(d0, s)
    p0 = abs(d0[ms][0])
    leg = seq(D, ms, mp + 1)
    pull = seq(D, mp + 1, me + 1)
    base = [v[4] for _, v in seq(D, ms - BASE, ms)]
    qmed = med(base)
    F["leg_min"], F["pull_min"], F["hist_min"] = mp - ms, me - mp, len(base)
    pk = max(v[1] for _, v in leg)
    F["leg_bps"] = bps(D[ms][0], pk, p0)
    F["end_bps"] = bps(D[ms][0], D[me][3], p0)
    q_leg = sum(v[4] for _, v in leg)
    F["q_leg_usd"] = q_leg
    F["turnover_x"] = q_leg / (qmed * len(leg)) if qmed and len(leg) else None
    F["turnover_x_total"] = sum(v[4] for _, v in seq(D, ms, me + 1)) / (qmed * (me - ms + 1)) if qmed else None
    # --- 12. откат в ноге, premium/discount
    rng = pk - D[ms][0]
    if pull and rng > 0:
        mn = min(v[2] for _, v in pull)
        depth = (pk - mn) / rng
        F["retrace_depth"] = depth
        mnc = min(v[3] for _, v in pull)
        F["retrace_depth_close"] = (pk - mnc) / rng
        for r in G["RET"]:
            F["retrace_hit_%g" % r] = int(depth >= r)
            t = next((m - mp for m, v in pull if (pk - v[2]) / rng >= r), None)
            F["retrace_t_%g" % r] = t
        F["end_pos_in_leg"] = (D[me][3] - D[ms][0]) / rng
    # --- 5,6,9. пулы ликвидности, снятие, возврат телом
    mpk = max(leg, key=lambda x: x[1][1])[0]  # минута максимума ноги
    for H in G["H"]:
        hist = seq(D, ms - H, ms)
        for k in G["K"]:
            fr = [hist[i][1][1] for i in fractals(hist, k, True) if hist[i][0] + k < ms]
            pre = "sw_H%d_k%d" % (H, k)
            F[pre + "_pools"] = len(fr)
            if not fr:
                continue
            top = max(fr)
            F[pre + "_swept_n"] = sum(1 for x in fr if x < pk)
            F[pre + "_top_over_bps"] = bps(top, pk, p0)
            for N in G["N"]:
                for T in G["T"]:
                    if pk > top:
                        F["%s_ret_N%d_T%d" % (pre, N, T)] = ret_close(D, mpk, top, N, T, p0)
        # закрепление телом над пулом (пробой, а не прокол): закрытий выше максимума H в ноге
    for T in G["T"]:
        hist = seq(D, ms - 240, ms)
        if hist:
            top = max(v[1] for _, v in hist)
            F["body_hold_closes_H240_T%d" % T] = sum(1 for _, v in leg if v[3] >= top + T / 1e4 * p0)
    # --- 7. защищённые/незащищённые свинги в ноге
    for k in G["K"]:
        hi_ix, lo_ix = fractals(leg, k, True), fractals(leg, k, False)
        prot = tot = 0
        for i in lo_ix:
            pj = [j for j in hi_ix if j < i]
            if not pj:
                continue
            j = pj[-1]
            pl = [x for x in lo_ix if x < j]
            if not pl:
                continue
            lvl = leg[pl[-1]][1][2]
            tot += 1
            if min(v[2] for _, v in leg[j + 1:i + 1]) < lvl:
                prot += 1
        F["swing_k%d_n" % k] = tot
        F["swing_k%d_unprot_share" % k] = (tot - prot) / tot if tot else None
    runmax, run, dd, hi = 0, 0, 0.0, leg[0][1][1]
    for i in range(1, len(leg)):
        run = run + 1 if leg[i][1][3] > leg[i - 1][1][3] else 0
        runmax = max(runmax, run)
        hi = max(hi, leg[i][1][1])
        dd = max(dd, hi - leg[i][1][2])
    F["leg_max_up_run"] = runmax
    F["leg_max_pullback_bps"] = dd / p0 * 1e4
    F["leg_compression"] = F["leg_bps"] / max(F["leg_max_pullback_bps"], 1.0)
    # --- 8. FVG (бычий в ориентации пампа) и заполнение после пика
    allb = seq(D, ms, me + 1)
    for fm in G["FVGMIN"]:
        gaps = []
        for i in range(1, len(leg) - 1):
            lo, hi2 = leg[i - 1][1][1], leg[i + 1][1][2]
            if hi2 > lo and (hi2 - lo) / p0 * 1e4 >= fm:
                gaps.append((i, lo, hi2))
        pre = "fvg_min%d" % fm
        F[pre + "_n"] = len(gaps)
        F[pre + "_cover_share"] = sum(g[2] - g[1] for g in gaps) / rng if rng > 0 else None
        if gaps and pull:
            for nm, g in (("big", max(gaps, key=lambda g: g[2] - g[1])), ("last", gaps[-1])):
                mnp = min(v[2] for _, v in pull)
                size = g[2] - g[1]
                F["%s_%s_size_bps" % (pre, nm)] = size / p0 * 1e4
                F["%s_%s_edge" % (pre, nm)] = int(mnp <= g[2])
                F["%s_%s_mid" % (pre, nm)] = int(mnp <= (g[1] + g[2]) / 2)
                F["%s_%s_full" % (pre, nm)] = int(mnp <= g[1])
                F["%s_%s_body_cancel" % (pre, nm)] = int(any(v[3] < g[1] for _, v in pull))
    # --- 10. AMD с диапазоном
    for R in G["R"]:
        a = seq(D, ms - R, ms - R // 3)
        if len(a) < R // 3:
            continue
        rl, rh = min(v[2] for _, v in a), max(v[1] for _, v in a)
        pre = "amd_R%d" % R
        F[pre + "_range_bps"] = (rh - rl) / p0 * 1e4
        rr = []
        st = R // 3
        for z in range(ms - BASE - R, ms - R, st):
            b = seq(D, z, z + (R - st))
            if len(b) >= (R - st) // 2:
                rr.append(max(v[1] for _, v in b) - min(v[2] for _, v in b))
        mr = med(rr)
        F[pre + "_range_vs_base"] = (rh - rl) / mr if mr else None
        for KM in G["KM"]:
            zone = seq(D, ms - R // 3, ms + KM)
            sw = [i for i, (_, v) in enumerate(zone) if v[2] < rl]
            ok = int(any(any(u[3] >= rl for _, u in zone[i:]) for i in sw[:1])) if sw else 0
            F["%s_manip_K%d" % (pre, KM)] = ok
        F[pre + "_break"] = int(pk > rh)
    # --- 9'. старший ТФ: максимум последних D суток до старта (день/неделя), вынос и возврат телом
    for Dd in (1, 7):
        hist = seq(D, ms - Dd * 1440, ms)
        F["htf_%dd_hist_min" % Dd] = len(hist)
        if len(hist) >= Dd * 1440 * 0.5:
            top = max(v[1] for _, v in hist)
            F["htf_%dd_over_bps" % Dd] = bps(top, pk, p0)
            for N in G["N"]:
                for T in G["T"]:
                    if pk > top:
                        F["htf_%dd_ret_N%d_T%d" % (Dd, N, T)] = ret_close(D, mpk, top, N, T, p0)
    # --- 4,12. спот и BTC: раскорреляция, базис, лидерство, SMT
    r_f = rets(D, ms - BASE, me + 1)
    spd = candles(croot, ex, "spot", spot_name(ex, sym)) if mk == fut_mkt(ex) else {}
    F["spot_present"] = int(bool(spd))
    if spd:
        S = orient(spd, s)
        ps = [m for m in range(ms, me + 1) if m in S and m in D]
        if S.get(ms) and D.get(ms):
            F["spot_start_ok"] = 1
            Sp0 = abs(spd[ms][0])
            F["spot_leg_bps"] = bps(S[ms][0], max(v[1] for _, v in seq(S, ms, mp + 1)), Sp0)
            F["spot_minus_fut_leg_bps"] = F["spot_leg_bps"] - F["leg_bps"]
            F["spot_end_bps"] = bps(S[ms][0], S[me][3], Sp0) if me in S else None
        r_s = rets(S, ms - BASE, me + 1)
        rw_f = {m: v for m, v in r_f.items() if ms < m <= me}
        rw_s = {m: v for m, v in r_s.items() if ms < m <= me}
        rb_f = {m: v for m, v in r_f.items() if m <= ms}
        rb_s = {m: v for m, v in r_s.items() if m <= ms}
        c_w, n_w = pair_corr(rw_f, rw_s)
        c_b, n_b = pair_corr(rb_f, rb_s)
        F["spot_corr_win"], F["spot_corr_base"], F["spot_corr_n"] = c_w, c_b, n_w
        F["spot_decorr"] = (c_b - c_w) if c_w is not None and c_b is not None else None
        for lag in (-3, -1, 1, 3):  # lag>0: спот отстаёт на lag минут от фьючерса
            c, _ = pair_corr(rw_f, rw_s, lag)
            F["spot_xcorr_lag%+d" % lag] = c
        # базис в bps: (фьюч - спот)/спот по закрытию минуты; ориентация знака не нужна, берём исходные цены
        def basis(m):
            return bps(spd[m][3], d0[m][3], abs(spd[m][3])) if m in spd and m in d0 else None
        b0, bp, b1 = basis(ms - 1), basis(mp), basis(me)
        F["basis_pre_bps"], F["basis_peak_bps"], F["basis_end_bps"] = b0, bp, b1
        bb = [basis(m) for m in range(ms - BASE, ms)]
        bb = [x for x in bb if x is not None]
        bmed = med(bb)
        F["basis_base_med_bps"] = bmed
        F["basis_peak_vs_base_bps"] = (bp - bmed) if bp is not None and bmed is not None else None
        F["basis_peak_minus_pre_bps"] = (bp - b0) if bp is not None and b0 is not None else None
        for H in (60, 240):  # SMT фьюч-спот: кто вынес максимум последних H минут
            hf = seq(D, ms - H, ms)
            hs = seq(S, ms - H, ms)
            if hf and hs:
                f_sw = int(max(v[1] for _, v in seq(D, ms, mp + 1)) > max(v[1] for _, v in hf))
                s_sw = int(max(v[1] for _, v in seq(S, ms, mp + 1)) > max(v[1] for _, v in hs)) if seq(S, ms, mp + 1) else None
                F["smt_fs_H%d_fut_swept" % H], F["smt_fs_H%d_spot_swept" % H] = f_sw, s_sw
                F["smt_fs_H%d_diverge" % H] = int(s_sw is not None and f_sw != s_sw)
    btc = candles(croot, ex, fut_mkt(ex), btc_name(ex)) if sym != btc_name(ex) else {}
    if btc:
        B = orient(btc, s)
        r_b = rets(B, ms - BASE, me + 1)
        rw = {m: v for m, v in r_f.items() if ms < m <= me}
        rbb = {m: v for m, v in r_f.items() if m <= ms}
        cw, _ = pair_corr(rw, {m: v for m, v in r_b.items() if ms < m <= me})
        cb, _ = pair_corr(rbb, {m: v for m, v in r_b.items() if m <= ms})
        F["btc_corr_win"], F["btc_corr_base"] = cw, cb
        F["btc_decorr"] = (cb - cw) if cw is not None and cb is not None else None
        if ms in B and me in B:
            F["btc_end_bps"] = bps(B[ms][0], B[me][3], abs(btc[ms][0]))
            F["btc_leg_bps"] = bps(B[ms][0], max(v[1] for _, v in seq(B, ms, mp + 1)), abs(btc[ms][0])) if seq(B, ms, mp + 1) else None
        for H in (60, 240):
            hb = seq(B, ms - H, ms)
            lb = seq(B, ms, mp + 1)
            hf = seq(D, ms - H, ms)
            if hb and lb and hf:
                c_sw = int(pk > max(v[1] for _, v in hf))
                b_sw = int(max(v[1] for _, v in lb) > max(v[1] for _, v in hb))
                F["smt_btc_H%d_coin_swept" % H], F["smt_btc_H%d_btc_swept" % H] = c_sw, b_sw
                F["smt_btc_H%d_diverge" % H] = int(c_sw != b_sw)
    # --- OI / фандинг / ликвидности
    if ex == "bybit" and mk == "linear":
        oi = read_5m("%s/bybit/oi/%s.csv.gz" % (oroot, sym), ["open_interest"])
        fu = read_5m("%s/bybit/funding/%s.csv.gz" % (oroot, sym), ["funding_rate"])
    elif ex == "gate":
        oi = read_5m("%s/gate/oi/%s.csv.gz" % (oroot, sym), ["open_interest", "long_liq_usd_new", "short_liq_usd_new"])
        fu = read_5m("%s/gate/funding/%s.csv.gz" % (oroot, sym), ["funding_rate"])
    else:
        oi = fu = None
    if oi:
        col_oi = "open_interest"

        def oi_at(t):
            r = at_or_before(oi, t)
            return r[1][col_oi] if r and t - r[0] <= 900000 and r[1].get(col_oi) else None

        t0, tp, t1 = ms * 60000, mp * 60000, me * 60000
        o_s, o_p, o_e = oi_at(t0), oi_at(tp), oi_at(t1)
        F["oi_leg_pct"] = (o_p / o_s - 1) * 100 if o_s and o_p else None
        F["oi_pull_pct"] = (o_e / o_p - 1) * 100 if o_p and o_e else None
        for R in G["R"]:
            o_b = oi_at(t0 - R * 60000)
            F["oi_pre%d_pct" % R] = (o_s / o_b - 1) * 100 if o_s and o_b else None
        if ex == "gate":
            ts = [a[0] for a in oi]
            for nm, a, b in (("pre180", t0 - 180 * 60000, t0), ("leg", t0, tp + 60000), ("pull", tp + 60000, t1 + 60000)):
                L = sum(r[1].get("long_liq_usd_new") or 0 for r in oi if a <= r[0] < b)
                Sx = sum(r[1].get("short_liq_usd_new") or 0 for r in oi if a <= r[0] < b)
                F["liq_long_usd_" + nm], F["liq_short_usd_" + nm] = L, Sx
            bs = [(r[1].get("long_liq_usd_new") or 0) + (r[1].get("short_liq_usd_new") or 0) for r in oi if t0 - BASE * 60000 <= r[0] < t0]
            F["liq_base_mean_5m_usd"] = sum(bs) / len(bs) if bs else None
    if fu:
        r = at_or_before(fu, ms * 60000)
        F["funding_last"] = r[1]["funding_rate"] if r else None
        b3 = [x[1]["funding_rate"] for x in fu if ms * 60000 - 3 * 86400000 <= x[0] < ms * 60000]
        F["funding_base3d_mean"] = sum(b3) / len(b3) if b3 else None
    # --- 1. поглощение по сделкам (Bybit linear)
    if ex == "bybit" and mk == "linear":
        tr = trades_min(troot, sym, ms - 15, me)
        F["trades_present"] = int(tr is not None)
        if tr:
            buy = {m: (v[0] if s > 0 else v[1]) for m, v in tr.items()}  # агрессор «по ходу»
            sel = {m: (v[1] if s > 0 else v[0]) for m, v in tr.items()}
            dl = sum(buy.get(m, 0) - sel.get(m, 0) for m in range(ms, mp + 1))
            tl = sum(buy.get(m, 0) + sel.get(m, 0) for m in range(ms, mp + 1))
            F["flow_delta_leg_usd"], F["flow_imb_leg"] = dl, dl / tl if tl else None
            F["flow_delta_per_bps"] = dl / F["leg_bps"] if F["leg_bps"] > 0 else None
            for N in G["ABS_N"]:
                a = [m for m in range(mp - N + 1, mp + 1) if m in tr]
                if not a:
                    continue
                db = sum(buy.get(m, 0) - sel.get(m, 0) for m in a)
                tt = sum(buy.get(m, 0) + sel.get(m, 0) for m in a)
                imb = db / tt if tt else 0
                disp = bps(D[a[0]][0], D[a[-1]][3], p0) if a[0] in D and a[-1] in D else None
                F["abs_N%d_imb" % N], F["abs_N%d_disp_bps" % N] = imb, disp
                F["abs_N%d_surge" % N] = tt / (qmed * len(a)) if qmed else None
                for th in G["IMB"]:
                    for sg in G["SURGE"]:
                        for dp in G["DISP"]:
                            F["abs_N%d_imb%g_sg%g_disp%d" % (N, th, sg, dp)] = int(
                                imb >= th and disp is not None and disp <= dp and qmed is not None and tt >= sg * qmed * len(a))
    return F


def work(a):
    w, roots, G = a
    try:
        return w, features(w, roots, G)
    except Exception as e:  # окно не должно ронять прогон
        return w, {"na": "ошибка: %s" % repr(e)[:150]}


def main():
    av = sys.argv[1:]
    opt = {"--candles": "/srv/alpha-sda/candles-1m", "--tk415": "/srv/alpha-sda/tk415/out", "--trades": "/data/tk417", "--grid": None, "--procs": "1"}
    pos = []
    i = 0
    while i < len(av):
        if av[i] in opt:
            opt[av[i]] = av[i + 1]
            i += 2
        else:
            pos.append(av[i])
            i += 1
    inp, outp = pos
    G = dict(GRID)
    if opt["--grid"]:
        G.update(json.load(open(opt["--grid"])))
    W = []
    with open(inp, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        h = rd.fieldnames
        c = {"ex": col(h, "биржа", "exchange"), "mk": col(h, "рынок", "market"), "sym": col(h, "инструмент", "монета", "symbol"),
             "t0": col(h, "t_start", "начало_utc"), "tp": col(h, "t_peak", "пик_utc"), "t1": col(h, "t_end", "конец_utc"),
             "s": col(h, "знак", "sign", "направление"), "id": col(h, "id", "event_id")}
        for n, r in enumerate(rd):
            s = 0
            if c["s"] and r[c["s"]]:
                s = {"памп": 1, "дамп": -1, "+1": 1, "1": 1, "-1": -1}.get(r[c["s"]].strip().lower(), 0)
            W.append({"id": r[c["id"]] if c["id"] else str(n), "ex": r[c["ex"]], "mk": r[c["mk"]], "sym": r[c["sym"]],
                      "t0": tms(r[c["t0"]]), "tp": tms(r[c["tp"]]), "t1": tms(r[c["t1"]]), "s": s})
    roots = (opt["--candles"], opt["--tk415"], opt["--trades"])
    procs = int(opt["--procs"])
    # окна одного инструмента — в один процесс (кэш свечей); порядок сохраняется
    if procs > 1:
        with Pool(procs) as p:
            res = p.map(work, [(w, roots, G) for w in W], chunksize=8)
    else:
        res = [work((w, roots, G)) for w in W]
    keys = []
    for _, F in res:
        for k in F:
            if k not in keys:
                keys.append(k)
    base = ["id", "биржа", "рынок", "инструмент", "t_start_ms", "t_peak_ms", "t_end_ms"]
    with open(outp, "w", newline="", encoding="utf-8") as g:
        wr = csv.writer(g)
        wr.writerow(base + keys)
        for w, F in res:
            wr.writerow([w["id"], w["ex"], w["mk"], w["sym"], w["t0"], w["tp"], w["t1"]] + [fnum(F.get(k)) for k in keys])
    na = sum(1 for _, F in res if "na" in F)
    print("окон %d, без признаков %d, колонок признаков %d -> %s" % (len(W), na, len(keys), outp))


if __name__ == "__main__":
    main()
