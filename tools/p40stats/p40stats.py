"""П-40 r2 этап 1: статистика §3.5, §5 (контраст признака, бутстреп, BH/Холм, кандидат, мощность).
Вход — таблица эпизодов (dict numpy-массивов одной длины), схема SCHEMA ниже; согласована комментарием в TK-500.
Реальных данных модуль не читает. Числа — из §8 П-40 r2: seed 40, BH q 0,10, Холм 0,05, интервалы 90 %."""
import numpy as np
from scipy import stats as sps

SEED = 40
Q_BH, ALPHA_HOLM, CI_LEVEL = 0.10, 0.05, 0.90
MIN_ASSET_DAYS, MIN_EPISODES, MIN_ASSETS = 30, 200, 5       # §8: на каждую страту (ветку контраста)
Z95, Z80 = 1.645, 0.84
BUDGET = 242                                                 # §5.1

# Схема таблицы эпизодов (одна строка = эпизод при главных θ=12,6, T_life=60 с, L_ms=500 мс).
# asset: int id; day: int номер суток; pair: int пара бирж; conv_H: 0/1 «сошлось за H»; mpu_X: 0/1 «МПУ > X»;
# net_bps: float итог; признаки f_<имя>: терцили 0/1/2, бинарные 0/1, час суток 0..3; пропуск = -1.
FEATURES = {  # 12 тестируемых (без «пара бирж» и «нога Bybit», §3.5)
    "depth": "ord", "spread": "ord", "hour": "hour", "vol5m": "ord", "plevel": "ord", "phi_sign": "bin",
    "oi5m": "ord", "liq": "bin", "flow1m": "ord", "bookimb": "ord", "d_arm": "ord", "leader": "bin"}
OUTCOMES = ("conv_H", "mpu_X")
SCHEMA = ["asset", "day", "pair", "conv_H", "mpu_X", "net_bps"] + ["f_" + k for k in FEATURES]


# ---------- контраст признака: достаточные статистики по ячейкам актив×сутки ----------
def arms(f, kind, order=None):
    """(маска «верх», маска «низ») контраста §3.5: терцили 2 против 0; бинарный 1 против 0; час — блоки order=(верх,низ)."""
    if kind == "ord":
        return f == 2, f == 0
    if kind == "bin":
        return f == 1, f == 0
    if order is None:
        raise ValueError("для часа нужен order=(верх,низ), зафиксированный по D1")
    return f == order[0], f == order[1]


def hour_order(f, y):
    """Порядок блоков по доле исхода на D1 (верх = наибольшая доля, низ = наименьшая)."""
    sh = [y[f == b].mean() if (f == b).any() else np.nan for b in range(4)]
    return int(np.nanargmax(sh)), int(np.nanargmin(sh))


def cell_stats(t, feat, out, order=None, n_a=None, n_d=None):
    """S[a,d,:] = (n_верх, k_верх, n_низ, k_низ)."""
    hi, lo = arms(t["f_" + feat], FEATURES[feat], order)
    n_a = n_a or int(t["asset"].max()) + 1
    n_d = n_d or int(t["day"].max()) + 1
    idx = t["asset"] * n_d + t["day"]
    y = t[out].astype(float)
    one = np.ones_like(y)
    S = np.stack([np.bincount(idx[m], weights=w[m], minlength=n_a * n_d)
                  for m, w in ((hi, one), (hi, y), (lo, one), (lo, y))], -1)
    return S.reshape(n_a, n_d, 4)


def effect_of(tot):
    tot = np.asarray(tot, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return tot[..., 1] / tot[..., 0] - tot[..., 3] / tot[..., 2]


def strata_ok(S):
    """§8: каждая ветка контраста — ≥30 актив×сутки, ≥200 эпизодов, ≥5 активов."""
    ok = True
    for n_col in (0, 2):
        n = S[:, :, n_col]
        ok &= (n > 0).sum() >= MIN_ASSET_DAYS and n.sum() >= MIN_EPISODES and (n.sum(1) > 0).sum() >= MIN_ASSETS
    return bool(ok)


# ---------- бутстреп ----------
def _units(S, scheme):
    if scheme == "day":
        return S.sum(0)
    if scheme == "asset":
        return S.sum(1)
    if scheme == "cluster":
        U = S.reshape(-1, 4)
        return U[U[:, 0] + U[:, 2] > 0]
    raise ValueError(scheme)


def boot_effects(S, scheme="day", B=2000, rng=None):
    """Распределение эффекта. day — блочный бутстреп по суткам (все активы суток вместе, §5.1, главный);
    cluster — ячейки актив×сутки независимо; asset — по активам; twoway — активы и сутки независимо."""
    rng = rng or np.random.default_rng(SEED)
    if scheme == "twoway":
        n_a, n_d, _ = S.shape
        wa = rng.multinomial(n_a, np.full(n_a, 1 / n_a), B).astype(float)
        wd = rng.multinomial(n_d, np.full(n_d, 1 / n_d), B).astype(float)
        tot = np.einsum("bdk,bd->bk", (wa @ S.reshape(n_a, -1)).reshape(B, n_d, 4), wd)
    else:
        U = _units(S, scheme)
        u = len(U)
        tot = rng.multinomial(u, np.full(u, 1 / u), B).astype(float) @ U
    e = effect_of(tot)
    return e[np.isfinite(e)]


def contrast(S, scheme="day", B=2000, ci="t", rng=None):
    """Эффект, 90 %-й интервал, p (двустор.). ci='pct' — перцентильный; 't' — e ± t_{G-1}·SE_бут (G — число единиц)."""
    tot = S.reshape(-1, 4).sum(0)
    e = float(effect_of(tot))
    be = boot_effects(S, scheme, B, rng)
    se = float(be.std(ddof=1)) if len(be) > 2 else np.nan
    if scheme == "cluster":
        g = len(_units(S, "cluster")) - 1
    elif scheme == "iid":
        g = int(tot[0] + tot[2]) - 1
    else:
        g = {"day": S.shape[1], "asset": S.shape[0], "twoway": min(S.shape[:2])}[scheme] - 1
    tq = sps.t.ppf(0.5 + CI_LEVEL / 2, max(g, 1))
    if ci == "pct":
        lo, hi = np.quantile(be, [(1 - CI_LEVEL) / 2, 0.5 + CI_LEVEL / 2])
    else:
        lo, hi = e - tq * se, e + tq * se
    p = float(2 * sps.t.sf(abs(e) / se, max(g, 1))) if se > 0 else np.nan
    return dict(effect=e, lo=float(lo), hi=float(hi), se=se, p=p, n=int(tot[0] + tot[2]), g=g)


def contrast_iid(t, feat, out, order=None, B=2000, rng=None):
    """Наивный бутстреп по эпизодам — справка (показывает, как сужается интервал без кластеров)."""
    rng = rng or np.random.default_rng(SEED)
    hi, lo = arms(t["f_" + feat], FEATURES[feat], order)
    y = t[out].astype(float)
    m = hi | lo
    U = np.stack([hi[m], hi[m] * y[m], lo[m], lo[m] * y[m]], -1).astype(float)
    return contrast(U[:, None, :], "cluster", B, "pct", rng) | dict(scheme="iid")


# ---------- χ² однородности (час суток df=3; «пара бирж» df=k-1) с поправкой на кластеры ----------
def chi2_homog(t, group, out, B=1000, rng=None):
    """χ² однородности долей исхода по группам: p наивный и p с поправкой Рао–Скотта (1-й порядок): χ²/d̄,
    d̄ = среднее по группам отношения бутстреп-дисперсии доли (блоки — сутки) к биномиальной, не меньше 1."""
    rng = rng or np.random.default_rng(SEED)
    g = np.asarray(group)
    keys = np.unique(g[g >= 0])
    y = t[out].astype(float)
    n_d = int(t["day"].max()) + 1
    N = np.stack([np.bincount(t["day"][g == k], minlength=n_d) for k in keys], -1).astype(float)
    K1 = np.stack([np.bincount(t["day"][g == k], weights=y[g == k], minlength=n_d) for k in keys], -1)
    n, k1 = N.sum(0), K1.sum(0)
    pj, pbar = k1 / n, k1.sum() / n.sum()
    chi2 = float((n * (pj - pbar) ** 2).sum() / (pbar * (1 - pbar)))
    w = rng.multinomial(n_d, np.full(n_d, 1 / n_d), B).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        pb = (w @ K1) / (w @ N)
    var_b = np.nanvar(pb, axis=0, ddof=1)
    deff = np.clip(var_b / (pj * (1 - pj) / n), 1, None)
    dbar = float(np.nanmean(deff))
    df = len(keys) - 1
    return dict(chi2=chi2, df=df, p_naive=float(sps.chi2.sf(chi2, df)), p_rs=float(sps.chi2.sf(chi2 / dbar, df)), dbar=dbar)


# ---------- множественные проверки ----------
def bh(p, q=Q_BH):
    """Бенджамини–Хохберг на фактическом числе m определённых p; булев вектор «прошёл»."""
    p = np.asarray(p, float)
    ok = np.isfinite(p)
    m = int(ok.sum())
    out = np.zeros(len(p), bool)
    if m == 0:
        return out
    idx = np.where(ok)[0][np.argsort(p[ok])]
    below = np.where(p[idx] <= q * np.arange(1, m + 1) / m)[0]
    if len(below):
        out[idx[: below.max() + 1]] = True
    return out


def holm(p, alpha=ALPHA_HOLM):
    p = np.asarray(p, float)
    m = len(p)
    out = np.zeros(m, bool)
    for r, i in enumerate(np.argsort(p)):
        if p[i] > alpha / (m - r):
            break
        out[i] = True
    return out


# ---------- проверки «кандидата» §3.5 (п. 1–4) ----------
def leave_top_k(S, k=3, B=2000, rng=None, ci="t"):
    """Без k лучших активов и k лучших суток разом (лучшие = наибольший вклад в эффект по leave-one-out)."""
    tot = S.reshape(-1, 4).sum(0)
    e0 = float(effect_of(tot))
    sg = 1.0 if e0 >= 0 else -1.0
    inf_a = np.array([sg * (e0 - float(effect_of(tot - S[a].sum(0)))) for a in range(S.shape[0])])
    inf_d = np.array([sg * (e0 - float(effect_of(tot - S[:, d].sum(0)))) for d in range(S.shape[1])])
    ka, kd = np.argsort(-inf_a)[:k], np.argsort(-inf_d)[:k]
    keep_a = np.setdiff1d(np.arange(S.shape[0]), ka)
    keep_d = np.setdiff1d(np.arange(S.shape[1]), kd)
    r = contrast(S[np.ix_(keep_a, keep_d)], "day", B, ci, rng)
    r.update(dropped_assets=ka.tolist(), dropped_days=kd.tolist())
    return r


def same_side(r, sign):
    """90 %-й интервал не накрывает 0 и лежит по ту же сторону, что sign."""
    return bool(r["lo"] > 0) if sign > 0 else bool(r["hi"] < 0)


def candidate(r1, r2, bh_pass, loo, ctrl_list):
    """Правило §3.5: п.1 знак и интервалы D1, D2 (одна сторона с эффектом D1); п.2 BH на D1∪D2 (bh_pass);
    п.3 loo (без 3+3); п.4 каждый контроль: |эффект| < ½|эффекта| и интервал накрывает 0."""
    s = float(np.sign(r1["effect"]))
    c1 = s != 0 and same_side(r1, s) and same_side(r2, s)
    c3 = s != 0 and same_side(loo, s)
    c4 = all(abs(c["effect"]) < 0.5 * abs(r1["effect"]) and c["lo"] <= 0 <= c["hi"] for c in ctrl_list)
    return dict(c1=bool(c1), c2=bool(bh_pass), c3=bool(c3), c4=bool(c4), candidate=bool(c1 and bh_pass and c3 and c4))


# ---------- мощность §5.2 ----------
def deff_icc(cluster, y):
    """DEFF=1+(m̄−1)ρ, ρ — дисперсионный (ANOVA) по кластерам; не меньше 1."""
    cl, inv = np.unique(cluster, return_inverse=True)
    m = np.bincount(inv).astype(float)
    ybar = np.bincount(inv, weights=y) / m
    n, kk = len(y), len(cl)
    msb = (m * (ybar - y.mean()) ** 2).sum() / (kk - 1)
    msw = ((y - ybar[inv]) ** 2).sum() / (n - kk)
    m0 = (n - (m ** 2).sum() / n) / (kk - 1)
    rho = max((msb - msw) / (msb + (m0 - 1) * msw), 0.0)
    return float(1 + (n / kk - 1) * rho), float(rho)


def mde_prop_protocol(p, deff, n_eff):
    """Формула §5.2 дословно: 2,49·√(p(1−p)·DEFF/n)."""
    return (Z95 + Z80) * np.sqrt(p * (1 - p) * deff / n_eff)


def mde_prop_exact(p, deff, n_hi, n_lo):
    """Разность долей при размерах веток n_hi, n_lo (эпизодов): (z95+z80)·√(p(1−p)·DEFF·(1/n_hi+1/n_lo))."""
    return (Z95 + Z80) * np.sqrt(p * (1 - p) * deff * (1 / n_hi + 1 / n_lo))


def mde_mean(sigma, deff, n):
    """Для среднего (bps): (z95+z80)·σ·√(DEFF/n)."""
    return (Z95 + Z80) * sigma * np.sqrt(deff / n)


def budget_check(n_tests):
    if n_tests > BUDGET:
        raise ValueError(f"испытаний {n_tests} > бюджета {BUDGET} (§5.1)")
    return n_tests
