#!/usr/bin/env python3
"""TK-493, П-38 G-C2-2: НЕЗАВИСИМЫЙ наивный цикл по минутам (чистый Python, без numpy-векторов, без общих функций с conv_lib).

Пишется отдельно от conv_lib: считает s, g, нормальные наблюдения, взвод, сигнал, вход, выход, P&L по определениям протокола (§4-§6) минута за минутой.
Одна пара, без дедупликации (дедупликация проверяется отдельным тестом). Вход - те же сырые ряды (open/close ног, b, sigma, c, r6), что у векторной версии.
Сравнивается с conv_lib.PairSim в test_conv.py (G-C2-2: сделки, выходы, счётчики на >= 40 строках «пара x комбинация»).
"""
import math

nan = float("nan")
isf = math.isfinite
FEE_RT = 12.6
THETA_COST = 25.2


def naive_pair(oA, cA, oB, cB, c, b, sig, r6, cp, cfg, combo, period0, admit=True, border=None, admitted_q5=True, turnA=None, turnB=None,
               turn_floor=1000.0, fcA=None, fcB=None):
    """-> (trades, counters). trade = (t, f, u, f_out, dir, code, gross, net)."""
    N = len(cA)
    vA = [isf(x) for x in cA]
    vB = [isf(x) for x in cB]
    valid = [vA[i] and vB[i] for i in range(N)]
    s = [1e4 * (math.log(cA[i]) - math.log(cB[i])) if valid[i] else nan for i in range(N)]
    dI = [s[i] - b[i] - c[i] for i in range(N)]
    g = [abs(x) for x in dI]
    sgn = [(1.0 if x > 0 else (-1.0 if x < 0 else 0.0)) if isf(x) else 0.0 for x in dI]
    q4 = [valid[i] and isf(g[i]) and isf(sig[i]) and admit for i in range(N)]
    cnt = {"signal_total": 0, "entry_deferred": 0, "signal_expired": 0, "dedup_dropped": 0, "pair_excluded": 0, "exit_ffill": 0, "open_at_end": 0}
    trades = []
    if not admitted_q5:
        return trades, cnt

    def gent(i):
        return max(combo.theta, combo.k * sig[i], THETA_COST)

    def E(i):
        return q4[i] and g[i] >= gent(i)

    def R(i):
        if i - combo.p + 1 < 0:
            return False
        for j in range(combo.p):
            if not E(i - j):
                return False
            if sgn[i - j] != sgn[i]:
                return False
        return True

    def q1(i):
        return i >= 1 and valid[i] and valid[i - 1]

    def q3(i):
        if turnA is None:
            return True
        return turnA[i] >= turn_floor and turnB[i] >= turn_floor

    def q6(i):
        if not cfg.use_q6 or not isf(cp.m_shift):
            return True
        return isf(r6[i]) and r6[i] <= cp.m_shift

    def q7(i):
        if not cfg.use_q7:
            return True
        if isf(cp.q_c) and i >= 60 and abs(c[i] - c[i - 60]) > cp.q_c:
            return False
        if border is not None and border[i]:
            return False
        return True

    def a90_of(gv):
        if cp.a90_edges is None:
            return 0.0
        idx = -1
        for j, e in enumerate(cp.a90_edges):
            if gv >= e:
                idx = j
        idx = max(idx, 0)
        return cp.a90_vals[idx] if idx < len(cp.a90_vals) else nan

    def rng(i):
        if not cfg.ceiling:
            return True
        a = a90_of(g[i])
        return (cp.gmax - g[i] >= a) and (g[i] / sig[i] < cp.gsig_max)

    def C(i):
        return R(i) and q1(i) and q3(i) and q6(i) and q7(i) and rng(i)

    def normal(i):
        return q4[i] and g[i] < cfg.rearm_frac * gent(i)

    def runs(valid_leg, u):
        r = 0
        while u - r >= 0 and not valid_leg[u - r]:
            r += 1
        return r

    rest = period0
    obs = []
    phase = "rest"
    stops = 0
    excluded = False
    t = period0
    d_ = cfg.delay
    while t < N:
        if excluded:
            break
        if t < rest:
            t += 1
            continue
        if phase == "rest":
            if valid[t]:
                if normal(t):
                    obs.append(t)
                else:
                    obs = []
                if len(obs) >= cp.m_r and obs[-1] - obs[-cp.m_r] <= cfg.ls:
                    phase = "armed"
            t += 1
            continue
        if not C(t):
            t += 1
            continue
        # --- сигнал в конце минуты t
        cnt["signal_total"] += 1
        f = t + 1 + d_
        while f < N and not (valid[f] and valid[f - 1]):
            f += 1
        if f >= N:
            rest = N
            phase = "rest"
            obs = []
            t = N
            continue
        deferred = f > t + 1 + d_
        if f - t > cp.kstale:
            cnt["signal_expired"] += 1
            rest = t + cp.kstale + 1
            phase = "rest"
            obs = []
            t += 1
            continue
        if deferred:
            cnt["entry_deferred"] += 1
        dirv = sgn[t]
        b_in = b[t] + c[t]
        g_in = g[t]
        c_in = c[t]
        oA_in, oB_in = oA[f], oB[f]
        # --- выход
        res = None
        for u in range(f, N):
            code = None
            x = dirv * (s[u] - b_in) if valid[u] else nan
            if valid[u] and isf(cp.q_c) and abs(c[u] - c_in) > cp.q_c:
                code = "S3"
            elif max(runs(vA, u), runs(vB, u)) >= cp.kstale:
                code = "S2"
            elif cfg.ceiling and valid[u] and isf(cp.gmax) and x >= cp.gmax:
                code = "S1"
            elif combo.s_stop is not None and valid[u] and x >= combo.s_stop * g_in:
                code = "S0"
            elif valid[u] and x <= cfg.rho * g_in:
                code = "CV"
            elif u - f >= combo.T:
                code = "TO"
            if code is not None:
                res = (u, code)
                break
        phase = "rest"
        obs = []
        if res is None:
            # конец периода
            pA, pB = last_close(cA), last_close(cB)
            gross = 1e4 * dirv * ((pB / oB_in - 1) - (pA / oA_in - 1))
            fund = naive_funding(fcA, fcB, oA, oB, dirv, f, N, oA_in, oB_in)
            trades.append((t, f, N - 1, N - 1, dirv, "END", gross, gross - FEE_RT + fund))
            cnt["open_at_end"] += 1
            break
        u, code = res
        fo = u + 1 + d_
        if fo > N - 1:
            pA, pB = last_close(cA), last_close(cB)
            gross = 1e4 * dirv * ((pB / oB_in - 1) - (pA / oA_in - 1))
            fund = naive_funding(fcA, fcB, oA, oB, dirv, f, N, oA_in, oB_in)
            trades.append((t, f, N - 1, N - 1, dirv, "END", gross, gross - FEE_RT + fund))
            cnt["open_at_end"] += 1
            break
        okA = vA[fo - 1] and isf(oA[fo])
        okB = vB[fo - 1] and isf(oB[fo])
        pA = oA[fo] if okA else last_close(cA, fo - 1)
        pB = oB[fo] if okB else last_close(cB, fo - 1)
        if not (okA and okB):
            cnt["exit_ffill"] += 1
        gross = 1e4 * dirv * ((pB / oB_in - 1) - (pA / oA_in - 1))
        fund = naive_funding(fcA, fcB, oA, oB, dirv, f, fo, oA_in, oB_in)
        trades.append((t, f, u, fo, dirv, code, gross, gross - FEE_RT + fund))
        if code in ("S0", "S1", "S2", "S3"):
            stops += 1
            if stops >= cfg.n_stop_max:
                excluded = True
                cnt["pair_excluded"] += 1
            rest = u + combo.T
        else:
            rest = fo
        t = rest if rest > t else t + 1
    return trades, cnt


def last_close(c, upto=None):
    i = (len(c) - 1) if upto is None else upto
    while i >= 0 and not isf(c[i]):
        i -= 1
    return c[i] if i >= 0 else nan


def price_at(o, m):
    while m >= 0 and not isf(o[m]):
        m -= 1
    return o[m] if m >= 0 else nan


def naive_funding(fcA, fcB, oA, oB, dirv, f_in, f_out, oA_in, oB_in):
    """fcA/fcB - {минута: ставка}; Σ по минутам СТРОГО между f_in и f_out; цена ноги - последний конечный open не позже минуты."""
    acc = 0.0
    if fcA:
        for m, r in fcA.items():
            if f_in < m < f_out:
                acc += dirv * price_at(oA, m) * r / oA_in
    if fcB:
        for m, r in fcB.items():
            if f_in < m < f_out:
                acc -= dirv * price_at(oB, m) * r / oB_in
    return 1e4 * acc
