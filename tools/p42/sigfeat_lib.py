"""П-42 §13.17 (TK-588): библиотека признаков подхода к уровню, метки «пошло в пробой», эпизодов и статистики.

Всё считается в длинной раме (шорт — цены с обратным знаком), как в движке: уровень `R`, цена сделки `P = s·p`, зона входа —
расстояние `(R − P)/|R| ≤ E` под уровнем. Цены целые ×1e8. Время — мс от 01.09.2026 00:00 UTC (как `sig_ms` движка).
Без заглядывания: минутные окна — закрытые минуты ≤ t−1, тиковые — сделки строго раньше сигнальной сделки.
"""
from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass

import numpy as np

BASE_MS = 1788220800000  # 2026-09-01 00:00:00 UTC, мс от эпохи
DAYS_TOTAL = 74
N_MIN = DAYS_TOTAL * 1440
START_DAY = 14
END_DAY = 73
SCALE = 100_000_000
MIN_MS = 60_000
DAY_MS = 86_400_000
END_MS = DAYS_TOTAL * DAY_MS  # конец данных (13.11 24:00 UTC)
E_E8 = 300_000  # зона входа E = 0,3 % (e8)
TAU_PM = 200  # τ = 0,2 % (в 1e-5), как в движке
B_PM = 500  # 0,5 % — полоса проторговки §13.14 (б)
INF = 1 << 60
O1, O2, O3, O4 = 1, 2, 3, 4  # исходы: не дошла / касание / пробой раньше возврата / возврат раньше пробоя


# ───────────────────────────── данные ─────────────────────────────

def load_b42(path: str) -> dict[str, np.ndarray]:
    """Кэш минуток `.b42` движка: o, h, l, c, qv (int64, N_MIN); o == 0 — минутки без данных."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] != b"B42\x01" or int.from_bytes(head[4:8], "little") != N_MIN:
        raise ValueError(f"{path}: не .b42 нужной длины")
    a = np.fromfile(path, dtype="<i8", offset=8, count=5 * N_MIN).reshape(5, N_MIN)
    return {"o": a[0], "h": a[1], "l": a[2], "c": a[3], "qv": a[4]}


def day_date(d: int) -> str:
    import datetime as dt
    return (dt.date(2026, 9, 1) + dt.timedelta(days=d)).isoformat()


class Ticks:
    """Сделки монеты из zip aggTrades по суткам (LRU). Порядок как в файле; при убывании времени — устойчивая сортировка (как движок)."""

    def __init__(self, root: str, sym: str, keep: int = 4):
        self.root, self.sym, self.keep = root, sym, keep
        self.cache: dict[int, tuple[np.ndarray, np.ndarray] | None] = {}
        self.order: list[int] = []
        self.unsorted_days = 0

    def _read(self, d: int):
        import pandas as pd
        name = f"{self.sym}-aggTrades-{day_date(d)}"
        path = f"{self.root}/{self.sym}/{name}.zip"
        try:
            zf = zipfile.ZipFile(path)
        except FileNotFoundError:
            return None
        with zf:
            with zf.open(name + ".csv") as fh:
                df = pd.read_csv(fh, usecols=["price", "transact_time"],
                                 dtype={"price": np.float64, "transact_time": np.int64})
        t = df["transact_time"].to_numpy() - BASE_MS
        p = np.rint(df["price"].to_numpy() * 1e8).astype(np.int64)
        if len(t) > 1 and np.any(np.diff(t) < 0):
            self.unsorted_days += 1
            o = np.argsort(t, kind="stable")
            t, p = t[o], p[o]
        return t, p

    def day(self, d: int):
        if d in self.cache:
            return self.cache[d]
        v = self._read(d) if 0 <= d < DAYS_TOTAL else None
        self.cache[d] = v
        self.order.append(d)
        while len(self.order) > self.keep:
            self.cache.pop(self.order.pop(0), None)
        return v

    def span(self, a: int, b: int):
        """Сделки со временем в [a, b] (мс от 01.09) из всех нужных суток; (t, p, полно): полно = все сутки в [a, b] есть."""
        d0, d1 = max(a // DAY_MS, 0), min(b // DAY_MS, DAYS_TOTAL - 1)
        ts, ps, full = [], [], b < END_MS and a >= 0
        for d in range(d0, d1 + 1):
            v = self.day(d)
            if v is None:
                full = False
                continue
            t, p = v
            i0 = np.searchsorted(t, a, "left")
            i1 = np.searchsorted(t, b, "right")
            ts.append(t[i0:i1])
            ps.append(p[i0:i1])
        if not ts:
            return np.empty(0, np.int64), np.empty(0, np.int64), False
        return np.concatenate(ts), np.concatenate(ps), full


SIGSX_COLS = ["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"]


def read_sigsx(path: str, usecols=None):
    """`<side>-sigsx.csv` дампа `P42_SIGSX`: в заголовке бинаря прогона нет `natr_e8` (ошибка заголовка, строки — 10 полей), читаем по именам."""
    import pandas as pd
    return pd.read_csv(path, header=0, names=SIGSX_COLS, usecols=usecols, dtype={"sym": str, "members": str})


def read_levels_csv(path: str):
    """`<side>-levels.csv` дампа `P42_LEVELS`: в поле `members` внутри версии члены через запятую, поэтому делим только первые 6 запятых."""
    import pandas as pd
    rows = []
    with open(path, encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split(",")
        for line in fh:
            rows.append(line.rstrip("\n").split(",", 6))
    df = pd.DataFrame(rows, columns=head)
    for c in ("level", "born", "end", "first_bar"):
        df[c] = df[c].astype(np.int64)
    return df


# ───────────────────────────── метка ─────────────────────────────

def ceil_mul(absr: int, num: int, den: int) -> int:
    """⌈|R|·num/den⌉ в целых."""
    return -((-absr * num) // den)


def outcome(Pw: np.ndarray, R: int, x_add: int, f_floor: int = 0):
    """Исходы O1…O4 по ценам окна `Pw` (длинная рама) и опорной цене `R`.
    x_add — расстояние пробоя в e8 (пробой: P ≥ R + x_add); f_floor — глубина возврата в e8 (возврат: P < R − f_floor).
    Возвращает (исход, i_касания, i_пробоя, i_возврата) — индексы в `Pw`, INF при отсутствии."""
    if len(Pw) == 0:
        return O1, INF, INF, INF
    ge = Pw >= R
    if not ge.any():
        return O1, INF, INF, INF
    tc = int(ge.argmax())
    gx = Pw >= R + x_add
    tx = int(gx.argmax()) if gx.any() else INF
    after = Pw[tc + 1:] < R - f_floor
    tr = tc + 1 + int(after.argmax()) if after.any() else INF
    if tx < INF and (tr == INF or tx < tr):
        return O3, tc, tx, tr
    if tr < INF and (tx == INF or tr < tx):
        return O4, tc, tx, tr
    return O2, tc, tx, tr


def touch_window_outcome(t: np.ndarray, Pw: np.ndarray, tc: int, tprime_ms: int, R: int, x_add: int):
    """T от первой сделки на уровне: окно (tc, tc + T′]; исходы: 'x' — пробой раньше возврата, 'r' — возврат раньше пробоя, 'n' — ни то ни другое."""
    if tc >= INF:
        return None
    end = np.searchsorted(t, t[tc] + tprime_ms, "right")
    seg = Pw[tc + 1:end]
    gx = seg >= R + x_add
    rr = seg < R
    ix = int(gx.argmax()) if gx.any() else INF
    ir = int(rr.argmax()) if rr.any() else INF
    if ix < INF and (ir == INF or ix < ir):
        return "x"
    if ir < INF and (ix == INF or ir < ix):
        return "r"
    return "n"


def dist_e8(R: int, P: int) -> int:
    """Расстояние под уровнем ×1e8, как в движке: (R − P)·SCALE/|R| (усечение к нулю)."""
    n = (R - P) * SCALE
    q = abs(n) // abs(R)
    return q if n >= 0 else -q


def find_signal_tick(t: np.ndarray, P: np.ndarray, sig_ms: int, R: int, dist: int):
    """Индекс сигнальной сделки в срезе: время == sig_ms и расстояние == dist (первая такая); None — нет."""
    i0 = np.searchsorted(t, sig_ms, "left")
    i1 = np.searchsorted(t, sig_ms, "right")
    for i in range(i0, i1):
        if dist_e8(R, int(P[i])) == dist:
            return i
    return None


# ───────────────────────────── минутные признаки ─────────────────────────────

def long_frame(m: dict[str, np.ndarray], s: int):
    """high/low/open/close минуток в длинной раме."""
    if s > 0:
        return m["h"], m["l"], m["o"], m["c"]
    return -m["l"], -m["h"], -m["o"], -m["c"]


def natr_series(m: dict[str, np.ndarray], p: int = 30) -> np.ndarray:
    """NATR 1м/p как в движке (`natr_series`), e8; −1 — нет значения."""
    o, h, l, c = m["o"], m["h"], m["l"], m["c"]
    n = len(o)
    tr = np.zeros(n, np.int64)
    cnt = np.zeros(n, np.int64)
    prevc = 0
    ho = h - l
    for t in range(n):  # прямой цикл — как в Rust (prevc зависит от прошлых)
        if o[t] != 0:
            x = ho[t]
            if prevc != 0:
                x = max(x, abs(h[t] - prevc), abs(l[t] - prevc))
            tr[t] = x
            cnt[t] = 1
            prevc = c[t]
    ptr = np.concatenate([[0], np.cumsum(tr)])
    pcnt = np.concatenate([[0], np.cumsum(cnt)])
    out = np.full(n, -1, np.int64)
    for t in range(p - 1, n):
        if o[t] == 0:
            continue
        k = pcnt[t + 1] - pcnt[t + 1 - p]
        if k * 2 < p:
            continue
        s = int(ptr[t + 1] - ptr[t + 1 - p])
        out[t] = min((s * SCALE) // (int(k) * int(c[t])), 2**31 - 1)
    return out


def ffill_close(m: dict[str, np.ndarray]) -> np.ndarray:
    """Последнее закрытие с данными (float64); до листинга NaN."""
    c = np.where(m["o"] != 0, m["c"].astype(np.float64), np.nan)
    idx = np.where(m["o"] != 0, np.arange(len(c)), -1)
    np.maximum.accumulate(idx, out=idx)
    out = np.where(idx >= 0, c[np.maximum(idx, 0)], np.nan)
    return out


def count_runs(mask: np.ndarray) -> int:
    """Число серий True подряд."""
    if len(mask) == 0:
        return 0
    m = mask.astype(np.int8)
    return int(((m[1:] == 1) & (m[:-1] == 0)).sum() + m[0])


def level_features(m: dict[str, np.ndarray], s: int, t: int, R: int, members: list[tuple[int, int]],
                   born: int, first_bar: int, dist: int, natr_e8: int, tf_min: int = 60) -> dict:
    """Признаки качества уровня на минуту сигнала `t`: версия уровня (члены) известна движку; окна — закрытые минуты ≤ t−1.
    R — цена уровня в длинной раме; members — (бар, цена члена, реальная)."""
    H, L, O, C = long_frame(m, s)
    px = np.array([x[1] for x in members], dtype=np.float64)
    mean = px.mean()
    bars = [x[0] for x in members]
    spread_bp = (px.max() - px.min()) / mean * 1e4
    lp_real = abs(R)
    lpx = px.max() if s > 0 else px.min()
    f = {
        "n_piv": len(members),
        "spread_bp": spread_bp,
        "spread_tau": spread_bp / (TAU_PM / 10.0),  # τ = 0,2 % = 20 бп
        "ext_gap_bp": (lpx - lp_real) / lp_real * 1e4 if s > 0 else (lp_real - lpx) / lp_real * 1e4,
        "age_h": (t - first_bar * tf_min) / 60.0,
        "since_born_h": (t - born) / 60.0,
        "dist_bp": dist / 1e4,
    }
    a = first_bar * tf_min
    seg = np.arange(a, t)
    seg = seg[(seg >= 0) & (seg < N_MIN)]
    excl = np.zeros(len(seg), bool)
    for b in bars:
        excl |= (seg >= b * tf_min) & (seg < (b + 1) * tf_min)
    ok = (m["o"][seg] != 0) & ~excl
    pierce = ok & (H[seg] >= R)
    band = R - abs(R) * TAU_PM // 100_000
    touch = ok & (H[seg] >= band)
    f["pierce_ep"] = count_runs(pierce)
    f["pierce_max_bp"] = float(max((H[seg][pierce].max() - R) / abs(R) * 1e4, 0.0)) if pierce.any() else 0.0
    f["touch_ep"] = count_runs(touch)
    if touch.any():
        f["since_touch_h"] = (t - int(seg[touch][-1])) / 60.0
    else:
        f["since_touch_h"] = (t - (max(bars) + 1) * tf_min) / 60.0
    # глубина отскоков
    depths = []
    mem_sorted = sorted(members)
    for i, (b, p_) in enumerate(mem_sorted):
        lo = (b + 1) * tf_min
        hi = mem_sorted[i + 1][0] * tf_min if i + 1 < len(mem_sorted) else born
        hi = min(hi, t)
        if hi <= lo:
            continue
        rng = np.arange(lo, hi)
        rng = rng[m["o"][rng] != 0]
        if len(rng) == 0:
            continue
        if s > 0:
            depths.append((p_ - m["l"][rng].min()) / p_ * 1e4)
        else:
            depths.append((m["h"][rng].max() - p_) / p_ * 1e4)
    f["depth_min_bp"] = min(depths) if depths else float("nan")
    f["depth_mean_bp"] = float(np.mean(depths)) if depths else float("nan")
    return f


def fresh_prior_features(m: dict[str, np.ndarray], s: int, t: int, R: int, bars: list[int], first_bar: int, tf_min: int = 60) -> dict:
    """Свежесть уровня без самого подхода (TK-587, 17.11): последнее касание ДО начала текущего подхода.
    Начало подхода `a0` — минута после последней «ухода» минуты (с данными) перед `t`: low ≤ нижней границы зоны (дальше E под уровнем)
    или high ≥ R (касание уровня) — то же определение «ухода», что у эпизодов. Касание — минута с high ≥ R − τ вне баров-членов, строго
    раньше `a0`. since_touch_prior_h — часов от этого касания до `t`; касаний нет — от конца бара последнего члена (touch_prior_none = 1)."""
    H, L, _, _ = long_frame(m, s)
    p_lo = R - abs(R) * (E_E8 + 1) / SCALE
    lo = max(first_bar * tf_min, 0)
    seg = np.arange(lo, t)
    data = m["o"][seg] != 0
    away = data & ((L[seg] <= p_lo) | (H[seg] >= R))
    idx = np.flatnonzero(away)
    a0 = int(seg[idx[-1]]) + 1 if len(idx) else lo
    excl = np.zeros(len(seg), bool)
    for b in bars:
        excl |= (seg >= b * tf_min) & (seg < (b + 1) * tf_min)
    band = R - abs(R) * TAU_PM // 100_000
    touch = data & ~excl & (H[seg] >= band) & (seg < a0)
    f = {"approach_dur_min": float(t - a0)}
    if touch.any():
        f["since_touch_prior_h"] = (t - int(seg[touch][-1])) / 60.0
        f["touch_prior_none"] = 0
    else:
        f["since_touch_prior_h"] = (t - (max(bars) + 1) * tf_min) / 60.0
        f["touch_prior_none"] = 1
    return f


def sparse_features(m: dict[str, np.ndarray], first_valid: int, t: int) -> dict:
    o = m["o"]
    f = {}
    for name, w in (("emp2h", 120), ("emp6h", 360)):
        a = max(t - w, first_valid)
        n = t - a
        f[name] = (n - int((o[a:t] != 0).sum())) / n if n >= 30 else float("nan")
    a = t - 60
    flat = (o[a:t] != 0) & (m["h"][a:t] == m["l"][a:t])
    f["flat60"] = float(flat.sum()) / 60.0
    return f


def tick_sparse_features(tpre: np.ndarray, sig_ms: int) -> dict:
    """tpre — времена сделок строго раньше сигнальной (в окне [sig−60 мин, сигнал))."""
    t15 = int((tpre >= sig_ms - 15 * MIN_MS).sum())
    t60 = len(tpre)
    if t60 == 0:
        gap = 3600.0
    else:
        edges = np.concatenate([[sig_ms - 60 * MIN_MS], tpre, [sig_ms]])
        gap = float(np.diff(edges).max()) / 1000.0
    return {"tpm15": t15 / 15.0, "tpm60": t60 / 60.0, "gap60_s": gap}


def approach_features(m: dict[str, np.ndarray], cf: np.ndarray, s: int, t: int, R: int, natr: float) -> dict:
    H, L, O, C = long_frame(m, s)
    f = {}
    c1 = cf[t - 1]
    for n in (5, 15, 60):
        c0 = cf[t - 1 - n]
        f[f"mv{n}"] = s * (c1 - c0) / c1 / natr if (c1 == c1 and c0 == c0) else float("nan")
    o = m["o"]
    a, b = t - 60, t
    v = o[a:b] != 0
    if v.any():
        cc = m["c"][a:b][v].astype(np.float64)
        oo = m["o"][a:b][v].astype(np.float64)
        f["maxmv1"] = float((s * (cc - oo) / cc).max() / natr)
    else:
        f["maxmv1"] = float("nan")
    a = t - 15
    v = o[a:b] != 0
    if v.any():
        f["maxrng15"] = float(((m["h"][a:b][v] - m["l"][a:b][v]).astype(np.float64) / m["c"][a:b][v]).max() / natr)
    else:
        f["maxrng15"] = float("nan")
    band = abs(R) * B_PM // 100_000
    for name, w in (("cons60", 60), ("cons120", 120)):
        a = t - w
        v = o[a:b] != 0
        cl = C[a:b]
        f[name] = int((v & (cl < R) & (R - cl <= band)).sum())
    return f


def context_features(m: dict[str, np.ndarray], t: int, natr_e8: int) -> dict:
    qv = m["qv"]
    return {
        "natr_bp": natr_e8 / 1e4,
        "turn1h": float(qv[t - 60:t].sum()) / 1e5,
        "turn24h": float(qv[t - 1440:t].sum()) / 1e5 if t >= 1440 else float("nan"),
    }


# ───────────────────────────── корреляции ─────────────────────────────

def block_returns(m: dict[str, np.ndarray], k: int) -> np.ndarray:
    """Доходности блоков k минут (границы по UTC от 01.09): закрытие блока — последнее закрытие внутри; блок без данных или сосед без данных — NaN."""
    nb = N_MIN // k
    o = m["o"][: nb * k].reshape(nb, k)
    c = m["c"][: nb * k].reshape(nb, k)
    valid = (o != 0).any(axis=1)
    last = k - 1 - np.argmax((o != 0)[:, ::-1], axis=1)
    cb = np.where(valid, c[np.arange(nb), last].astype(np.float64), np.nan)
    r = np.full(nb, np.nan)
    r[1:] = cb[1:] / cb[:-1] - 1.0
    return r


def minute_returns(m: dict[str, np.ndarray]) -> np.ndarray:
    o, c = m["o"], m["c"].astype(np.float64)
    r = np.full(N_MIN, np.nan)
    ok = (o[1:] != 0) & (o[:-1] != 0)
    r[1:][ok] = c[1:][ok] / c[:-1][ok] - 1.0
    return r


def pearson_pairs(x: np.ndarray, y: np.ndarray, min_n: int) -> float:
    """Пирсон по позициям, где обе доходности не NaN; меньше min_n пар — NaN."""
    ok = ~(np.isnan(x) | np.isnan(y))
    n = int(ok.sum())
    if n < min_n:
        return float("nan")
    a, b = x[ok], y[ok]
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else float("nan")


def corr_matrix(R: np.ndarray, min_n: int):
    """Корреляции строк R (монеты × время, NaN — пропуск) по попарно полным наблюдениям; (корреляции, число наблюдений). Суммы через произведения масок."""
    M = (~np.isnan(R)).astype(np.float64)
    X = np.nan_to_num(R, nan=0.0)
    n = M @ M.T
    sx = X @ M.T
    sxx = (X * X) @ M.T
    sxy = X @ X.T
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = n * sxy - sx * sx.T
        vx = n * sxx - sx * sx
        den = np.sqrt(vx * vx.T)
        r = cov / den
    r[n < min_n] = np.nan
    np.fill_diagonal(r, 1.0)
    return r, n


def window_corr(coin_r: np.ndarray, ref_r: np.ndarray, b_last: int, n_blocks: int, min_n: int) -> float:
    a = max(b_last - n_blocks + 1, 0)
    return pearson_pairs(coin_r[a:b_last + 1], ref_r[a:b_last + 1], min_n)


# ───────────────────────────── статистика ─────────────────────────────

def auc(x: np.ndarray, y: np.ndarray) -> float:
    """AUC признака x против метки y∈{0,1}; ничья — ½. NaN в x исключаются."""
    from scipy.stats import rankdata
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok].astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(x)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def auc_bruteforce(x: np.ndarray, y: np.ndarray) -> float:
    pos, neg = x[y == 1], x[y == 0]
    tot = 0.0
    for a in pos:
        tot += (neg < a).sum() + 0.5 * (neg == a).sum()
    return tot / (len(pos) * len(neg))


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def block_resamples(days: np.ndarray, n_boot: int, block: int, rng: np.random.Generator):
    """Индексы строк для скользящих блоков по суткам: список массивов индексов."""
    ud = np.unique(days)
    nd = len(ud)
    idx_by_day = {d: np.flatnonzero(days == d) for d in ud}
    nb = math.ceil(nd / block)
    out = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(nd - block, 0) + 1, size=nb)
        sel = np.concatenate([ud[s:s + block] for s in starts])[:nd]
        out.append(np.concatenate([idx_by_day[d] for d in sel]) if len(sel) else np.empty(0, int))
    return out


def cluster_resamples(keys: np.ndarray, n_boot: int, rng: np.random.Generator):
    """Пересэмплирование кластеров (монет) целиком."""
    uk = np.unique(keys)
    idx_by = {k: np.flatnonzero(keys == k) for k in uk}
    out = []
    for _ in range(n_boot):
        sel = rng.choice(uk, size=len(uk), replace=True)
        out.append(np.concatenate([idx_by[k] for k in sel]))
    return out


def boot_auc(x, y, resamples):
    vals = []
    for ix in resamples:
        a = auc(x[ix], y[ix])
        if a == a:
            vals.append(a)
    return np.array(vals)


def boot_p(vals: np.ndarray, centre: float = 0.5) -> float:
    """p = 2·min(доля ≤ centre, доля ≥ centre), со сглаживанием +1."""
    if len(vals) == 0:
        return float("nan")
    lo = (vals <= centre).sum()
    hi = (vals >= centre).sum()
    return float(min(1.0, (2 * min(lo, hi) + 1) / (len(vals) + 1)))


def holm(ps: list[float]) -> list[float]:
    """Поправка Холма (скорректированные p)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    run = 0.0
    for rank, i in enumerate(order):
        v = (m - rank) * ps[i]
        run = max(run, v)
        adj[i] = min(1.0, run)
    return adj


def icc_binary(y: np.ndarray, groups: np.ndarray):
    """ρ (дисперсионный анализ) и эффективное число N/(1 + (m̄ − 1)ρ) для бинарной метки по кластерам."""
    y = y.astype(np.float64)
    n = len(y)
    ug, inv, cnt = np.unique(groups, return_inverse=True, return_counts=True)
    k = len(ug)
    if k < 2 or n <= k:
        return float("nan"), float(n)
    gm = np.bincount(inv, weights=y) / cnt
    grand = y.mean()
    ssb = float((cnt * (gm - grand) ** 2).sum())
    ssw = float(((y - gm[inv]) ** 2).sum())
    msb = ssb / (k - 1)
    msw = ssw / (n - k)
    n0 = (n - (cnt ** 2).sum() / n) / (k - 1)
    rho = (msb - msw) / (msb + (n0 - 1) * msw) if (msb + (n0 - 1) * msw) > 0 else 0.0
    rho = max(rho, 0.0)
    mbar = n / k
    return float(rho), float(n / (1 + (mbar - 1) * rho))


@dataclass
class Bucket:
    lo: float
    hi: float
    n: int
    k: int
