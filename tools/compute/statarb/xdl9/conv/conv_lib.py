#!/usr/bin/env python3
"""TK-493, П-38 r2 (вид C2, схождения): ядро счёта на numpy. Сигнал, автомат сценария, вход/выход, издержки, фандинг, MTM.

Что здесь (номера - разделы docs/research/P-38-convergence-xdl9.md):
  §4.1 спред s, базис b (медиана за прошлые L минут, пересчёт кратно 5, >= nb наблюдений), робастная sigma (1,4826*MAD, пол - тик), общий фактор ячейки c,
       d_idio = s - b - c; §5.4 короткий базис и отношение сдвига r6; §4.2 фильтры Q1, Q3, Q4, Q6, Q7 (Q5 - парный булев из conv_calib);
  §4.3 условие сигнала (g >= max(theta, k*sigma, 25,2) на p подряд минутах, запас до потолка G_max и A90, sigma-потолок);
  §4.4 вход по open[t+2] (откладывается до K_stale, не отбрасывается), выход по open[u+2] или по последней известной цене (без ожидания);
  §5.5, §6 автомат ПОКОЙ -> ВЗВЕДЕНО -> СИГНАЛ -> В ПОЗИЦИИ -> ВЫХОД -> ПОКОЙ | ПАУЗА | ИСКЛЮЧЕНА; выходы CV, S0..S3, сессия, таймаут, конец периода (MTM);
  §4.3 дедупликация события ноги (одна минута, общая нога - остаётся пара с наименьшим ключом); §8 издержки 12,6 bps, фандинг двухногий.

Решения реализации там, где текст протокола допускает два прочтения (все - в report_md и открытых пунктах; на смысл не влияют, но фиксируют код):
  R1  условие сигнала проверяется поминутно: E_tau = valid & (g_tau >= max(theta, k*sigma_tau, 25,2)) на каждой из p минут со своим sigma_tau (sigma меняется раз в 5 мин);
  R2  короткий базис b_s,t - медиана s за минуты [t-60, t) (текущая не входит, как у b); при менее чем 20 наблюдениях Q6 не выполнено (вход запрещён);
  R3  взвод: m_r последних ВАЛИДНЫХ наблюдений пары, все минуты которых >= rest_from (после выхода/паузы), все нормальные (g < 0,5*g_entry), первое не старше 60 минут;
      взвод держится до сигнала; сигнал - первая минута после взвода, где C[t] истинно;
  R4  после CV, сессии, таймаута rest_from = u+2 (минута заполнения выхода); после стопа rest_from = u + C_stop (C_stop = T); после signal_expired rest_from = t+K_stale+1;
      после deduplicated-сигнала rest_from = t+1; N_stop = 3 - пара исключается ПОСЛЕ третьего стопа (§0.2 «не больше 3 стопов... потом исключена»);
  R5  выход у ноги: open[u+2], если close[u+1] и open[u+2] этой ноги конечны; иначе последний известный close этой ноги (<= u+1); решение u+2 > последняя минута - позиция
      открыта на конце периода (MTM по последним валидным close ног минус 12,6 bps полного круга и фандинг);
  R6  S3 и условия CV/S0/S1 проверяются на минутах с обеими ногами валидными; S2, сессия и таймаут идут по часам; приоритет S3 > S2 > S1 > S0 > CV > сессия > таймаут;
  R7  сессионные классы: каждая непрерывная серия «в сессии» - своя сессия (обеденный перерыв Гонконга = закрытие); серия, упирающаяся в начало/конец данных, не считается
      открытием/закрытием (запрета на границе данных и принудительного выхода нет);
  R8  фандинг: Σ ставок на минутах (f_in, f_out) строго между, нога по цене последнего конечного open (как d2_exact_funding.py); знак: P&L-эффект (net = gross - fee + fund).
Только numpy и stdlib. Потоки: никаких, детерминизм (порядок обработки пар и событий задан ключами).
"""
import heapq
import math
from collections import Counter, namedtuple
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

MIN_DAY = 1440
FEE_SIDE = 3.15                    # taker, bps на сторону на ногу (В-47, В-88)
FEE_RT = 4 * FEE_SIDE              # 12,6 bps круг двух ног
FEE_MAKER_RT = 4 * 1.26            # 5,04 bps - нижняя граница издержек
THETA_COST = 2 * FEE_RT            # 25,2 bps = entry_m x 12,6
MAD_CONST = 1.4826
JUMP_LN = math.log(1.4)
CODES = ["CV", "S0", "S1", "S2", "S3", "SES", "TO", "END"]
CODE = {n: i for i, n in enumerate(CODES)}
PRIO = {"S3": 0, "S2": 1, "S1": 2, "S0": 3, "CV": 4, "SES": 5, "TO": 6}
STOPS = ("S0", "S1", "S2", "S3")
TRADE_FIELDS = ["t_sig", "f_in", "u_dec", "f_out", "dir", "g_in", "sig_in", "code", "gross", "fee", "fund", "net", "deferred", "ffill", "jump", "stale",
                "turn_in", "turn_out", "cap", "hold", "mtm", "pnl_a", "pnl_b"]
Combo = namedtuple("Combo", "theta k p T s_stop")        # T - минуты, s_stop - float или None («только S1»)


@dataclass
class Cfg:
    L: int = 1440
    step: int = 5
    nb: int = 240
    ls: int = 60
    nbs: int = 20
    rho: float = 0.25
    n_stop_max: int = 3
    rearm_frac: float = 0.5
    s_open_ban: int = 60
    s_close_ban: int = 3
    delay: int = 1                 # +1 мин: вход open[t+2], выход open[u+2]; 0 - open[t+1]; справочно 2, 3
    entry_mode: str = "defer"      # 'defer' (основной) | 'drop' (r1: Q2) | 'close' (по close[t])
    exit_mode: str = "ffill"       # 'ffill' (основной) | 'wait' (r1: ждать первой пары валидных минут)
    dedup: bool = True
    ceiling: bool = True           # False: без запрета по потолку и без S1 (гипотетика «без запретов»)
    use_q6: bool = True            # False: Q6 выключено
    use_q7: bool = True
    stale_frac_win: int = 60


@dataclass
class ClassParams:
    name: str = "crypto"
    gmax: float = math.inf
    gsig_max: float = math.inf
    a90_edges: object = None       # np.ndarray границ корзин g_in (25,2; 40; 80; ...)
    a90_vals: object = None        # A90 по корзине (nan - опоры нет, вход запрещён)
    kstale: int = 60
    q_c: float = math.inf
    m_shift: float = math.inf
    m_r: int = 5
    q_stale: float = 0.9
    session: bool = False


# ------------------------------------------------------------------ служебные
def next_true(a):
    """nxt[i] = наименьший j >= i с a[j], иначе len(a); длина len(a)+1."""
    n = len(a)
    idx = np.where(a, np.arange(n, dtype=np.int64), n)
    out = np.empty(n + 1, dtype=np.int64)
    out[:n] = np.minimum.accumulate(idx[::-1])[::-1]
    out[n] = n
    return out


def ffill_idx(valid):
    """Индекс последней валидной минуты <= i (или -1)."""
    n = len(valid)
    return np.maximum.accumulate(np.where(valid, np.arange(n, dtype=np.int64), -1))


def nan_runs(valid, active=None):
    """Длина серии подряд идущих пустых минут, заканчивающейся в i (0 в валидной минуте). active - маска «в сессии»: минуты вне неё не считаются и не сбрасывают."""
    n = len(valid)
    if active is None:
        li = ffill_idx(valid)
        r = np.arange(n) - li
        r[valid] = 0
        return r.astype(np.int32)
    out = np.zeros(n, dtype=np.int32)
    ai = np.flatnonzero(active)
    if len(ai):
        va = valid[ai]
        li = ffill_idx(va)
        r = np.arange(len(ai)) - li
        r[va] = 0
        out[ai] = r
    return out


def rolling_sorted_median(w, cnt):
    """Медиана по строкам матрицы w, где недопустимые значения = +inf, а cnt - число допустимых в строке. Сортирует w на месте."""
    w.sort(axis=1)
    lo = np.maximum((cnt - 1) // 2, 0)
    hi = cnt // 2
    r = np.arange(len(cnt))
    hi = np.minimum(hi, w.shape[1] - 1)
    return 0.5 * (w[r, lo] + w[r, hi])


# ------------------------------------------------------------------ §4.1 базис и sigma
def spread_series(cA, cB):
    valid = np.isfinite(cA) & np.isfinite(cB) & (cA > 0) & (cB > 0)
    s = np.full(len(cA), np.nan)
    s[valid] = 1e4 * (np.log(cA[valid]) - np.log(cB[valid]))
    return s, valid


def basis_sigma(s, L, step, nb, floor, mad_const=MAD_CONST, chunk=256):
    """b_t = медиана s по валидным минутам [max(t-L, 0), t), пересчёт на минутах, кратных step (индекс от начала ряда, начало кратно step);
    нужно >= nb наблюдений. sigma_t = max(mad_const*MAD, floor) по тем же минутам. -> (b, sig, cnt)."""
    N = len(s)
    nR = -(-N // step)
    sp = np.concatenate([np.full(L, np.nan), s])
    fin = np.isfinite(sp)
    cs = np.concatenate([[0], np.cumsum(fin)])
    starts = np.arange(nR) * step
    cnt = cs[starts + L] - cs[starts]
    b_r = np.full(nR, np.nan)
    mad_r = np.full(nR, np.nan)
    rows = np.flatnonzero(cnt >= nb)
    W = sliding_window_view(sp, L)
    for a in range(0, len(rows), chunk):
        ch = rows[a:a + chunk]
        w = W[starts[ch]].copy()
        w[~np.isfinite(w)] = np.inf
        c = cnt[ch]
        med = rolling_sorted_median(w, c)
        dv = np.abs(w - med[:, None])
        dv[~np.isfinite(dv)] = np.inf
        b_r[ch] = med
        mad_r[ch] = rolling_sorted_median(dv, c)
    b = np.repeat(b_r, step)[:N]
    mad = np.repeat(mad_r, step)[:N]
    sig = np.maximum(mad_const * mad, floor) if floor is not None else mad_const * mad
    return b, sig, np.repeat(cnt, step)[:N]


def short_ratio(s, b, sig, ls, nbs, chunk=4096):
    """r6_t = |b_s,t - b_t| / sigma_t, b_s,t - медиана s за [t-ls, t) (>= nbs наблюдений; R2); nan, где не определено."""
    N = len(s)
    sp = np.concatenate([np.full(ls, np.nan), s])
    fin = np.isfinite(sp)
    cs = np.concatenate([[0], np.cumsum(fin)])
    cnt = cs[np.arange(N) + ls] - cs[np.arange(N)]
    r6 = np.full(N, np.nan)
    rows = np.flatnonzero((cnt >= nbs) & np.isfinite(b) & np.isfinite(sig))
    W = sliding_window_view(sp, ls)
    for a in range(0, len(rows), chunk):
        ch = rows[a:a + chunk]
        w = W[ch].copy()
        w[~np.isfinite(w)] = np.inf
        bs = rolling_sorted_median(w, cnt[ch])
        r6[ch] = np.abs(bs - b[ch]) / sig[ch]
    return r6


def cell_common(D, n_min=5):
    """Общий фактор ячейки: медиана d по валидным парам в ту же минуту, если пар >= n_min, иначе 0. D: (пары, минуты), nan - не определено. -> (c, n_pairs_valid)."""
    P, N = D.shape
    fin = np.isfinite(D)
    cnt = fin.sum(axis=0)
    c = np.zeros(N)
    cols = np.flatnonzero(cnt >= n_min)
    if len(cols):
        for a in range(0, len(cols), 2048):
            ch = cols[a:a + 2048]
            w = np.where(fin[:, ch], D[:, ch], np.inf).T.copy()
            c[ch] = rolling_sorted_median(w, cnt[ch])
    return c, cnt


def tick_floor_bps(c_day0, minvalid_steps=50):
    """Тик пары: МОДА положительных шагов close ноги за сутки разогрева / медиана close, в bps. c_day0 - close одной ноги за сутки 2465 (nan - пусто).
    -> bps или nan, если < 50 ненулевых шагов. Моду берём по шагам, округлённым до 1e-10 доли медианной цены; при равных частотах - наименьший шаг."""
    v = np.isfinite(c_day0)
    both = v[1:] & v[:-1]
    st = np.abs(c_day0[1:] - c_day0[:-1])[both]
    st = st[st > 0]
    if len(st) < minvalid_steps:
        return float("nan")
    med = float(np.nanmedian(c_day0))
    if not med > 0:
        return float("nan")
    q = np.round(st / med, 10)
    u, cn = np.unique(q, return_counts=True)
    return float(u[int(np.argmax(cn))]) * 1e4


def roll_half_spread(c):
    """Оценка Ролла полуспреда a = sqrt(-cov(r_t, r_{t-1})) в bps по лог-доходностям close на валидных соседних тройках минут; nan, если cov >= 0 или мало данных."""
    v = np.isfinite(c)
    ok = v[2:] & v[1:-1] & v[:-2]
    lc = np.log(np.where(v, c, 1.0))
    r = lc[1:] - lc[:-1]
    x, y = r[1:][ok], r[:-1][ok]
    if len(x) < 30:
        return float("nan")
    cov = float(np.mean((x - x.mean()) * (y - y.mean())))
    return math.sqrt(-cov) * 1e4 if cov < 0 else float("nan")


# ------------------------------------------------------------------ серия пары
class PairSeries:
    """Все ряды одной пары на минутах [m0, m0+N). Индексы - относительные (i = абс.минута - m0)."""

    def __init__(self, oA, cA, oB, cB, m0, cfg, *, key=("p",), legs=None, exA="", exB="", cls="crypto", asset="", b=None, sig=None, r6=None, c=None,
                 floor=0.0, admit=True, turnA=None, turnB=None, sess=None, frA=None, frB=None, semA="first_trade", semB="first_trade"):
        self.cfg, self.m0, self.key = cfg, int(m0), key
        self.N = N = len(cA)
        self.exA, self.exB, self.cls, self.asset = exA, exB, cls, asset
        self.legs = legs if legs is not None else ((exA, "A" + str(key)), (exB, "B" + str(key)))
        self.semA, self.semB = semA, semB
        self.oA, self.cA, self.oB, self.cB = oA, cA, oB, cB
        self.vA, self.vB = np.isfinite(cA), np.isfinite(cB)
        self.s, self.valid = spread_series(cA, cB)
        self.admit = bool(admit)
        if b is None:
            b, sig, _ = basis_sigma(self.s, cfg.L, cfg.step, cfg.nb, floor if floor is not None else np.nan)
            r6 = short_ratio(self.s, b, sig, cfg.ls, cfg.nbs)
        self.b, self.sig, self.r6 = b.astype(np.float64), sig.astype(np.float64), r6.astype(np.float64)
        self.c = np.zeros(N) if c is None else c.astype(np.float64)
        dI = self.s - self.b - self.c
        self.dI = dI
        self.g = np.abs(dI)
        self.sgn = np.sign(dI)
        self.turnA, self.turnB = turnA, turnB
        # ffill цены и последние валидные минуты
        self.lvA, self.lvB = ffill_idx(self.vA), ffill_idx(self.vB)
        self.lcA = np.where(self.lvA >= 0, cA[np.maximum(self.lvA, 0)], np.nan)
        self.lcB = np.where(self.lvB >= 0, cB[np.maximum(self.lvB, 0)], np.nan)
        oAf, oBf = ffill_idx(np.isfinite(oA)), ffill_idx(np.isfinite(oB))
        self.ffA = np.where(oAf >= 0, oA[np.maximum(oAf, 0)], np.nan)
        self.ffB = np.where(oBf >= 0, oB[np.maximum(oBf, 0)], np.nan)
        # сессия
        self.sess_in = np.ones(N, bool) if sess is None else sess.astype(bool)
        self.has_sess = sess is not None
        self.runA, self.runB = nan_runs(self.vA, self.sess_in if self.has_sess else None), nan_runs(self.vB, self.sess_in if self.has_sess else None)
        self.sess_dec = np.full(N, -1, np.int64)
        self.border = np.zeros(N, bool)
        if self.has_sess:
            self._build_session(cfg)
        # доля пустых минут за предыдущие 60 (в сессии)
        w = cfg.stale_frac_win
        act = self.sess_in.astype(np.int64)
        ea = np.concatenate([[0], np.cumsum((~self.vA & self.sess_in).astype(np.int64))])
        eb = np.concatenate([[0], np.cumsum((~self.vB & self.sess_in).astype(np.int64))])
        ca = np.concatenate([[0], np.cumsum(act)])
        lo = np.maximum(np.arange(N) - w, 0)
        den = (ca[np.arange(N)] - ca[lo]).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.staleA = np.where(den > 0, (ea[np.arange(N)] - ea[lo]) / den, np.nan)
            self.staleB = np.where(den > 0, (eb[np.arange(N)] - eb[lo]) / den, np.nan)
        # аномалия ячейки |c_t - c_{t-60}|
        self.anom = np.full(N, np.nan)
        if N > 60:
            self.anom[60:] = np.abs(self.c[60:] - self.c[:-60])
        # скачок 40 % за минуту на ноге; кумулятивно
        jA = np.zeros(N, bool)
        jB = np.zeros(N, bool)
        with np.errstate(invalid="ignore", divide="ignore"):
            ra = np.abs(np.log(cA[1:] / cA[:-1]))
            rb = np.abs(np.log(cB[1:] / cB[:-1]))
        jA[1:] = np.isfinite(ra) & (ra > JUMP_LN)
        jB[1:] = np.isfinite(rb) & (rb > JUMP_LN)
        self.jcs = np.concatenate([[0], np.cumsum(jA | jB)])
        # фандинг: ставка * цена ноги, накопленная (R8)
        self.cfA = self._fund_cum(frA, self.ffA)
        self.cfB = self._fund_cum(frB, self.ffB)
        self.nextV2 = next_true(self.valid & np.concatenate([[False], self.valid[:-1]]))

    @staticmethod
    def _fund_cum(fr, ff):
        if fr is None:
            return None
        return np.concatenate([[0.0], np.cumsum(np.where(fr != 0, fr * np.nan_to_num(ff), 0.0))])

    def _build_session(self, cfg):
        N = self.N
        a = np.concatenate([[False], self.sess_in, [False]]).astype(np.int8)
        d = np.diff(a)
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1) - 1            # включительно
        for st, en in zip(starts, ends):
            real_open = st > 0
            real_close = en < N - 1
            if real_close:
                self.sess_dec[st:en + 1] = (en + 1) - cfg.s_close_ban
                self.border[max(en + 1 - cfg.s_close_ban, st):en + 1] = True
            if real_open:
                self.border[st:min(st + cfg.s_open_ban, en + 1)] = True
        self.border |= ~self.sess_in

    def day_of(self, i):
        return (self.m0 + i) // MIN_DAY


def gentry_array(sig, theta, k):
    return np.maximum(np.maximum(theta, k * sig), THETA_COST)


def a90_lookup(cp, g):
    """A90 для g по корзинам класса; nan = опоры нет."""
    if cp.a90_edges is None:
        return np.zeros_like(g)
    e = np.asarray(cp.a90_edges)
    v = np.asarray(cp.a90_vals, dtype=np.float64)
    idx = np.clip(np.searchsorted(e, g, side="right") - 1, 0, None)
    out = np.where(idx < len(v), v[np.minimum(idx, len(v) - 1)], np.nan)
    return out


class Feats:
    """Признаки пары для сетки: маски Q, кандидаты C и указатели вперёд по (theta, k, p), структуры взвода по (theta, k). Считаются один раз на пару."""

    def __init__(self, ps, cp, cfg, p0=0):
        self.ps, self.cp, self.cfg, self.p0 = ps, cp, cfg, p0
        v = ps.valid
        N = ps.N
        self.q1 = v & np.concatenate([[False], v[:-1]])
        if ps.turnA is not None and ps.turnB is not None:
            self.q3 = (ps.turnA >= TURN_FLOOR) & (ps.turnB >= TURN_FLOOR)
        else:
            self.q3 = np.ones(N, bool)
        self.q4 = np.isfinite(ps.g) & np.isfinite(ps.sig) & ps.admit & v
        with np.errstate(invalid="ignore"):
            if cfg.use_q6 and math.isfinite(cp.m_shift):
                self.q6 = np.isfinite(ps.r6) & (ps.r6 <= cp.m_shift)
            else:
                self.q6 = np.ones(N, bool)
            anom_ok = (~(ps.anom > cp.q_c)) if math.isfinite(cp.q_c) else np.ones(N, bool)
        self.q7 = (anom_ok & ~ps.border) if cfg.use_q7 else np.ones(N, bool)
        self.qbase = self.q1 & self.q3 & self.q4 & self.q6 & self.q7
        self.qcs = np.concatenate([[0], np.cumsum(self.qbase.astype(np.int64))])
        a90 = a90_lookup(cp, ps.g) if cfg.ceiling else np.zeros(N)
        with np.errstate(invalid="ignore"):
            self.rng = ((cp.gmax - ps.g >= a90) & (ps.g / ps.sig < cp.gsig_max)) if cfg.ceiling else np.ones(N, bool)
        self._cand = {}
        self._arm = {}
        self.counts = Counter()

    def cand(self, theta, k, p):
        key = (theta, k, p)
        if key in self._cand:
            return self._cand[key]
        ps, N = self.ps, self.ps.N
        gent = gentry_array(ps.sig, theta, k)
        with np.errstate(invalid="ignore"):
            E = self.q4 & (ps.g >= gent)
        R = E.copy()
        for j in range(1, p):
            R[j:] &= E[:-j] & (ps.sgn[j:] == ps.sgn[:-j])
            R[:j] = False
        C = R & self.q1 & self.q3 & self.q6 & self.q7 & self.rng
        nxt = next_true(C)
        sl = slice(self.p0, N)
        cn = Counter()
        cn["R"] = int(R[sl].sum())
        cn["drop_Q1"] = int((R & ~self.q1)[sl].sum())
        cn["drop_Q3"] = int((R & ~self.q3)[sl].sum())
        cn["drop_Q6"] = int((R & ~self.q6)[sl].sum())
        cn["drop_Q7"] = int((R & ~self.q7)[sl].sum())
        cn["drop_range"] = int((R & ~self.rng)[sl].sum())
        self._cand[key] = (C, nxt, cn)
        return self._cand[key]

    def arm(self, theta, k):
        key = (theta, k)
        if key in self._arm:
            return self._arm[key]
        ps, cfg, cp = self.ps, self.cfg, self.cp
        vi = np.flatnonzero(ps.valid)
        gent = gentry_array(ps.sig, theta, k)
        with np.errstate(invalid="ignore"):
            nrm = self.q4 & (ps.g < cfg.rearm_frac * gent)
        no = nrm[vi]
        idx = np.arange(len(vi))
        run = idx - np.maximum.accumulate(np.where(~no, idx, -1))
        m_r = max(1, int(cp.m_r))
        span = np.zeros(len(vi), bool)
        if len(vi) >= m_r:
            span[m_r - 1:] = (vi[m_r - 1:] - vi[:len(vi) - m_r + 1]) <= cfg.ls
        armok = (run >= m_r) & span
        self._arm[key] = (vi, next_true(armok))
        return self._arm[key]


TURN_FLOOR = 1000.0         # Q3: 1 000 единиц валюты котировки на ногу в минуту сигнала (назначено без Судьи)


# ------------------------------------------------------------------ автомат одной пары
class PairSim:
    def __init__(self, ps, cp, cfg, combo, feats, period0, admitted=True):
        self.ps, self.cp, self.cfg, self.combo, self.f = ps, cp, cfg, combo, feats
        self.period0 = period0
        self.rest_from = period0
        self.n_stops = 0
        self.excluded = False
        self.admitted = admitted
        self.trades = []
        self.cnt = Counter()
        self.armed = []
        self.arm_min = None
        self.stop_codes = Counter()
        if not admitted:
            self.cnt["drop_Q5"] += 1

    @property
    def key(self):
        return self.ps.key

    # -- поиск следующего сигнала
    def next_signal(self):
        if self.excluded or not self.admitted or not self.ps.admit:
            return None
        th, k, p = self.combo.theta, self.combo.k, self.combo.p
        vi, nxt = self.f.arm(th, k)
        j0 = int(np.searchsorted(vi, self.rest_from))
        js = j0 + max(1, int(self.cp.m_r)) - 1
        N = self.ps.N
        if js >= len(vi):
            return None
        a = int(nxt[js])
        if a >= len(vi):
            return None
        a_min = int(vi[a])
        C, nc, _ = self.f.cand(th, k, p)
        t = int(nc[a_min + 1])
        self.arm_min = a_min
        if t >= N:
            self.armed.append((a_min, N - 1))
            return None
        return t

    def drop_dedup(self, t):
        self.armed.append((self.arm_min, t))
        self.cnt["dedup_dropped"] += 1
        self.rest_from = t + 1

    # -- исполнение сигнала
    def execute(self, t):
        ps, cp, cfg, cb = self.ps, self.cp, self.cfg, self.combo
        N = ps.N
        self.armed.append((self.arm_min, t))
        self.cnt["signal_total"] += 1
        d = cfg.delay
        if cfg.entry_mode == "close":
            f = t
            fin = True
            deferred = False
        else:
            f = int(ps.nextV2[min(t + 1 + d, N)])
            deferred = f > t + 1 + d
            if f >= N:
                self.cnt["signal_noend"] += 1
                self.rest_from = N
                return
            if cfg.entry_mode == "drop" and deferred:
                self.cnt["drop_Q2_r1"] += 1
                self.rest_from = t + 1
                return
            fin = f - t <= cp.kstale
            if fin and ps.has_sess:
                fin = bool(ps.sess_in[f]) and (ps.sess_dec[f] < 0 or f < ps.sess_dec[f])
        if not fin:
            self.cnt["signal_expired"] += 1
            self.rest_from = t + cp.kstale + 1
            return
        if deferred:
            self.cnt["entry_deferred"] += 1
        sgn = float(ps.sgn[t])
        b_in = float(ps.b[t] + ps.c[t])
        g_in = float(ps.g[t])
        sig_in = float(ps.sig[t])
        c_in = float(ps.c[t])
        if cfg.entry_mode == "close":
            oA_in, oB_in = float(ps.cA[t]), float(ps.cB[t])
        else:
            oA_in, oB_in = float(ps.oA[f]), float(ps.oB[f])
        ex = self.find_exit(f, sgn, b_in, g_in, c_in)
        stale = bool((ps.staleA[t] > cp.q_stale) or (ps.staleB[t] > cp.q_stale))
        base = dict(t=t, f=f, d=sgn, g=g_in, sig=sig_in, oA=oA_in, oB=oB_in, deferred=deferred, stale=stale)
        if ex is None:
            self._close_mtm(base)
        else:
            self._close_at(base, ex[0], ex[1])

    def _fill_exit(self, u):
        """-> (pA, pB, ffill, fo, minA, minB) или None, если решение u невозможно исполнить внутри данных."""
        ps, cfg = self.ps, self.cfg
        N = ps.N
        d = cfg.delay
        fo = u + 1 + d
        if cfg.exit_mode == "wait":
            fo = int(ps.nextV2[min(fo, N)])
            if fo >= N:
                return None
            return float(ps.oA[fo]), float(ps.oB[fo]), False, fo, (fo - 1 if ps.semA == "chain" else fo), (fo - 1 if ps.semB == "chain" else fo)
        if fo > N - 1:
            return None
        okA = bool(ps.vA[fo - 1] and np.isfinite(ps.oA[fo]))
        okB = bool(ps.vB[fo - 1] and np.isfinite(ps.oB[fo]))
        pA = float(ps.oA[fo]) if okA else float(ps.lcA[fo - 1])
        pB = float(ps.oB[fo]) if okB else float(ps.lcB[fo - 1])
        mA = (fo - 1 if ps.semA == "chain" else fo) if okA else int(ps.lvA[fo - 1])
        mB = (fo - 1 if ps.semB == "chain" else fo) if okB else int(ps.lvB[fo - 1])
        return pA, pB, not (okA and okB), fo, mA, mB

    def _funding(self, base, f_in, f_out):
        ps = self.ps
        if ps.cfA is None and ps.cfB is None:
            return 0.0
        lo, hi = f_in, f_out - 1
        if hi <= lo:
            return 0.0
        acc = 0.0
        if ps.cfA is not None:
            acc += base["d"] * (ps.cfA[hi + 1] - ps.cfA[lo + 1]) / base["oA"]
        if ps.cfB is not None:
            acc -= base["d"] * (ps.cfB[hi + 1] - ps.cfB[lo + 1]) / base["oB"]
        return 1e4 * acc

    def _turn(self, mi, ma, mb):
        ps = self.ps
        if ps.turnA is None or ps.turnB is None:
            return float("nan")
        return float(min(ps.turnA[ma], ps.turnB[mb]))

    def _close_at(self, base, u, code):
        ps, cp, cfg = self.ps, self.cp, self.cfg
        fx = self._fill_exit(u)
        if fx is None:
            self._close_mtm(base)
            return
        pA, pB, ffill, fo, mA, mB = fx
        f, d = base["f"], base["d"]
        gross = 1e4 * d * ((pB / base["oB"] - 1.0) - (pA / base["oA"] - 1.0))
        fund = self._funding(base, f, fo)
        net = gross - FEE_RT + fund
        jump = bool(ps.jcs[min(fo, ps.N - 1) + 1] - ps.jcs[max(f - 1, 0) + 1] > 0) if fo > f else False
        # оборот минут, давших цены заполнения
        ent_m_a = f - 1 if ps.semA == "chain" else f
        ent_m_b = f - 1 if ps.semB == "chain" else f
        if cfg.entry_mode == "close":
            ent_m_a = ent_m_b = base["t"]
        if ps.turnA is not None and ps.turnB is not None:
            ti = float(min(ps.turnA[max(ent_m_a, 0)], ps.turnB[max(ent_m_b, 0)]))
            to = float(min(ps.turnA[max(mA, 0)], ps.turnB[max(mB, 0)]))
            cap = ti + to
        else:
            ti = to = cap = float("nan")
        self.trades.append((base["t"], f, u, fo, d, base["g"], base["sig"], CODE[code], gross, FEE_RT, fund, net, int(base["deferred"]), int(ffill), int(jump),
                            int(base["stale"]), ti, to, cap, fo - f, 0, pA / base["oA"] - 1.0, pB / base["oB"] - 1.0))
        self.cnt["exit_" + code] += 1
        if ffill:
            self.cnt["exit_ffill"] += 1
        if jump:
            self.cnt["jump_trades"] += 1
        if code in STOPS:
            self.n_stops += 1
            self.stop_codes[code] += 1
            if self.n_stops >= cfg.n_stop_max:
                self.excluded = True
                self.cnt["pair_excluded"] += 1
            self.rest_from = u + self.combo.T
        else:
            self.rest_from = fo

    def _close_mtm(self, base):
        ps, cp = self.ps, self.cp
        N = ps.N
        f, d = base["f"], base["d"]
        pA, pB = float(ps.lcA[N - 1]), float(ps.lcB[N - 1])
        gross = 1e4 * d * ((pB / base["oB"] - 1.0) - (pA / base["oA"] - 1.0))
        fund = self._funding(base, f, N)
        net = gross - FEE_RT + fund
        dead = max(int(ps.runA[N - 1]), int(ps.runB[N - 1])) >= cp.kstale
        if dead:
            self.cnt["dead_leg_end"] += 1
        jump = bool(ps.jcs[N] - ps.jcs[max(f - 1, 0) + 1] > 0)
        self.trades.append((base["t"], f, N - 1, N - 1, d, base["g"], base["sig"], CODE["END"], gross, FEE_RT, fund, net, int(base["deferred"]), 0, int(jump),
                            int(base["stale"]), float("nan"), float("nan"), float("nan"), N - 1 - f, 1, pA / base["oA"] - 1.0, pB / base["oB"] - 1.0))
        self.cnt["open_at_end"] += 1
        self.rest_from = N

    # -- выход
    def find_exit(self, f, d, b_in, g_in, c_in):
        ps, cp, cfg, cb = self.ps, self.cp, self.cfg, self.combo
        N = ps.N
        T = cb.T
        hi = min(f + T, N - 1)
        sl = slice(f, hi + 1)
        v = ps.valid[sl]
        x = d * (ps.s[sl] - b_in)
        cands = []

        def first(a):
            i = int(a.argmax())
            return i if a[i] else None
        with np.errstate(invalid="ignore"):
            if math.isfinite(cp.q_c):
                i = first(v & (np.abs(ps.c[sl] - c_in) > cp.q_c))
                if i is not None:
                    cands.append((i, PRIO["S3"], "S3"))
            i = first(np.maximum(ps.runA[sl], ps.runB[sl]) >= cp.kstale)
            if i is not None:
                cands.append((i, PRIO["S2"], "S2"))
            if cfg.ceiling and math.isfinite(cp.gmax):
                i = first(v & (x >= cp.gmax))
                if i is not None:
                    cands.append((i, PRIO["S1"], "S1"))
            if cb.s_stop is not None:
                i = first(v & (x >= cb.s_stop * g_in))
                if i is not None:
                    cands.append((i, PRIO["S0"], "S0"))
            i = first(v & (x <= cfg.rho * g_in))
            if i is not None:
                cands.append((i, PRIO["CV"], "CV"))
        if ps.has_sess and ps.sess_dec[f] >= f:
            rel = int(ps.sess_dec[f]) - f
            if rel <= hi - f:
                cands.append((rel, PRIO["SES"], "SES"))
        if f + T <= N - 1:
            cands.append((T, PRIO["TO"], "TO"))
        if not cands:
            return None
        i, _, code = min(cands)
        return f + i, code

    def active_days(self):
        """Сутки (абсолютные), где пара хотя бы одну минуту была взведена и Q1,Q3,Q4,Q5,Q6,Q7 выполнены (§6)."""
        if not self.admitted:
            return set()
        qcs = self.f.qcs
        days = set()
        for a, e in self.armed:
            if e < a:
                continue
            d0, d1 = self.ps.day_of(a), self.ps.day_of(e)
            for d in range(d0, d1 + 1):
                lo = max(a, d * MIN_DAY - self.ps.m0)
                hi = min(e, (d + 1) * MIN_DAY - 1 - self.ps.m0)
                if hi >= lo and qcs[hi + 1] - qcs[lo] > 0:
                    days.add(d)
        return days


def simulate_component(pairs, cp, cfg, combo, feats_list, period0, admitted=None):
    """Одна компонента (пары с общими ногами) на одной комбинации. Дедупликация события ноги: в минуту t сигналы пар с общей ногой - одно событие,
    остаётся пара с наименьшим ключом. -> список PairSim в порядке pairs."""
    sims = [PairSim(ps, cp, cfg, combo, ft, period0, True if admitted is None else bool(admitted[i])) for i, (ps, ft) in enumerate(zip(pairs, feats_list))]
    heap = []
    for i, sm in enumerate(sims):
        t = sm.next_signal()
        if t is not None:
            heapq.heappush(heap, (t, sm.key, i))
    kept = set()
    while heap:
        t, _, i = heapq.heappop(heap)
        sm = sims[i]
        legs = sm.ps.legs
        if cfg.dedup and any((leg, t) in kept for leg in legs):
            sm.drop_dedup(t)
        else:
            if cfg.dedup:
                for leg in legs:
                    kept.add((leg, t))
            sm.execute(t)
        nt = sm.next_signal()
        if nt is not None:
            heapq.heappush(heap, (nt, sm.key, i))
    return sims


def trades_array(sims):
    """Сделки всех пар компоненты -> (список кортежей, номер пары). """
    rows = []
    for i, sm in enumerate(sims):
        for tr in sm.trades:
            rows.append((i,) + tr)
    return rows


def day_tables(sims, period_last_day):
    """Суточные суммы по парам: {(i_пара, сутки): [Σ net закрытых, число закрытых]}, MTM {i_пара: (Σ net, число)} и активные пары-сутки {i: set(сутки)}."""
    day = {}
    mtm = {}
    for i, sm in enumerate(sims):
        for tr in sm.trades:
            r = dict(zip(TRADE_FIELDS, tr))
            if r["mtm"]:
                a = mtm.setdefault(i, [0.0, 0])
                a[0] += r["net"]
                a[1] += 1
            else:
                d = sm.ps.day_of(r["f_out"])
                a = day.setdefault((i, d), [0.0, 0])
                a[0] += r["net"]
                a[1] += 1
    act = {i: sm.active_days() for i, sm in enumerate(sims)}
    return day, mtm, act
