#!/usr/bin/env python3
"""TK-493, П-38 r2 §5.2-§5.5, §6, §7.1-§7.3: измерение структурных чисел класса на формировании (K4), полураспад пары (K5, Q5), анализ выживания (K6).

Все числа измеряются по записанным правилам, а не подбираются. Конвейеры: Pi_train читает только сутки 2465..2471, Pi_full - 2465..2478 (защита conv_guard).
  episodes  - эпизоды (theta = 25,2, k = 3, p = 1, без условия потолка и без Q6, взвод, Q1/Q3/Q4/Q7, дедупликация по общей ноге; b_in заморожен);
              исходы от минуты u >= t+2: (К) x <= g_in/4, (С) x >= 2 g_in, (Т) таймаут 1440 мин, цензура - конец периода; x = dir*(s_u - b_in)
  gmax_rule - накопленные вероятности (К), (С) к T_max (Аален-Йохансен), 90 %-границы кластерным бутстрепом (актив x сутки, B = 3000, seed 37); G_max = нижняя
              граница первой снизу корзины с опорой (>= 20 единиц), где верхняя граница P(К) < нижней границы P(С); иначе верхняя граница старшей корзины с опорой
              (метка «граница опоры данных»); A90 по корзине; T_struct (Каплан-Мейер, 0,99); таблица «был ли стоп прав»
  q5        - полураспад HL15 по регрессии x_t на x_{t-15}, блочный по суткам бутстреп (B = 3000, seed 37), верхняя граница 90 %-интервала
"""
import json
import math
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402

MIN_DAY = 1440
TMAX = 1440
B_BOOT = 3000
SEED = 37
BINS_BPS = [25.2, 40.0, 80.0, 160.0, 320.0, 640.0, 1280.0, 2560.0, 5120.0, 10240.0]
BINS_SIG = [3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0, 89.0, 144.0, 233.0]
N_UNITS_MIN = 20


# ------------------------------------------------------------------ эпизоды (K4)
class EpisodeSim(L.PairSim):
    """Автомат измерения: сигнал -> исход (К)/(С)/(Т)/цензура без входа и без отложенного заполнения; после исхода rest_from = u + 1."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.eps = []

    def execute(self, t):
        ps = self.ps
        N = ps.N
        self.armed.append((self.arm_min, t))
        d = float(ps.sgn[t])
        b_in = float(ps.b[t] + ps.c[t])
        g_in = float(ps.g[t])
        hi = min(t + TMAX, N - 1)
        u0 = t + 2
        out, u, adverse = 0, N - 1, g_in
        if u0 <= hi:
            v = ps.valid[u0:hi + 1]
            x = d * (ps.s[u0:hi + 1] - b_in)
            with np.errstate(invalid="ignore"):
                kv = v & (x <= self.cfg.rho * g_in)
                sv = v & (x >= 2.0 * g_in)
            ik = int(kv.argmax()) if kv.any() else None
            isv = int(sv.argmax()) if sv.any() else None
            cands = [(i, o) for i, o in ((isv, 2), (ik, 1)) if i is not None]
            if cands:
                i, out = min(cands)
                u = u0 + i
            elif t + TMAX <= N - 1:
                out, u = 3, hi
            else:
                out, u = 0, N - 1
            seg = x[:u - u0 + 1] if u >= u0 else x[:0]
            seg = seg[np.isfinite(seg)]
            adverse = max(g_in, float(seg.max())) if len(seg) else g_in
        self.eps.append((t, g_in, g_in / float(ps.sig[t]), out, u - t, adverse - g_in, self.ps.day_of(t), self.ps.asset, d, b_in))
        # после исхода (С): что было бы при продолжении удержания (K6): сошлось бы за T после стопа, дальнейший экстремум в долях g_in
        if out == 2:
            post = []
            for TT in (120, 360, 1440):
                e = min(u + TT, N - 1)
                if e > u:
                    vv = ps.valid[u + 1:e + 1]
                    xx = d * (ps.s[u + 1:e + 1] - b_in)
                    with np.errstate(invalid="ignore"):
                        hit = bool((vv & (xx <= self.cfg.rho * g_in)).any())
                    mx = float(np.nanmax(np.where(vv, xx, np.nan))) / g_in if vv.any() else float("nan")
                else:
                    hit, mx = False, float("nan")
                post.append((hit, mx))
            self.eps[-1] = self.eps[-1] + (post,)
        self.rest_from = u + 1 if out else N


def collect_episodes(pairs_ps, cp, cfg, p0, feats_cache=None):
    """pairs_ps - список PairSeries одной компоненты. -> список эпизодов (кортежи EpisodeSim.eps)."""
    cfg_m = L.Cfg(**{**cfg.__dict__, "ceiling": False, "use_q6": False})
    feats = [L.Feats(ps, L.ClassParams(name=cp.name, m_r=cp.m_r, kstale=cp.kstale, q_c=cp.q_c), cfg_m, p0) for ps in pairs_ps]
    combo = L.Combo(L.THETA_COST, 3, 1, TMAX, None)
    sims = [EpisodeSim(ps, cp, cfg_m, combo, ft, p0, True) for ps, ft in zip(pairs_ps, feats)]
    import heapq
    heap = []
    for i, sm in enumerate(sims):
        t = sm.next_signal()
        if t is not None:
            heapq.heappush(heap, (t, sm.key, i))
    kept = set()
    while heap:
        t, _, i = heapq.heappop(heap)
        sm = sims[i]
        if cfg_m.dedup and any((leg, t) in kept for leg in sm.ps.legs):
            sm.drop_dedup(t)
        else:
            for leg in sm.ps.legs:
                kept.add((leg, t))
            sm.execute(t)
        nt = sm.next_signal()
        if nt is not None:
            heapq.heappush(heap, (nt, sm.key, i))
    return [e for sm in sims for e in sm.eps]


# ------------------------------------------------------------------ Аален-Йохансен, Каплан-Мейер
def aj_cif(time, ev, horizon=TMAX):
    """Накопленные вероятности исходов 1 и 2 к horizon (конкурирующие риски, цензура ev = 0 и ev = 3 как цензура по К/С)."""
    time = np.asarray(time)
    ev = np.asarray(ev)
    o = np.argsort(time, kind="stable")
    t, e = time[o], ev[o]
    uq, idx = np.unique(t, return_index=True)
    nr = (len(t) - idx).astype(float)
    d1 = np.add.reduceat((e == 1).astype(float), idx)
    d2 = np.add.reduceat((e == 2).astype(float), idx)
    keep = uq <= horizon
    s_after = np.cumprod(1.0 - (d1 + d2) / nr)
    s_before = np.concatenate([[1.0], s_after[:-1]])
    return float((s_before * d1 / nr)[keep].sum()), float((s_before * d2 / nr)[keep].sum())


def aj_cluster_bootstrap(time, ev, cluster, B=B_BOOT, seed=SEED, horizon=TMAX):
    """90 %-границы (5 и 95 %) P(К) и P(С) к horizon: ресэмплируются кластеры (актив x сутки)."""
    time = np.asarray(time)
    ev = np.asarray(ev)
    cl, cinv = np.unique(np.asarray(cluster), return_inverse=True)
    C = len(cl)
    uq, tinv = np.unique(time, return_inverse=True)
    J = len(uq)
    E1 = np.zeros((C, J))
    E2 = np.zeros((C, J))
    EA = np.zeros((C, J))
    np.add.at(EA, (cinv, tinv), 1.0)
    np.add.at(E1, (cinv[ev == 1], tinv[ev == 1]), 1.0)
    np.add.at(E2, (cinv[ev == 2], tinv[ev == 2]), 1.0)
    keep = uq <= horizon
    rng = np.random.default_rng(seed)
    f1s, f2s = [], []
    for a in range(0, B, 250):
        b = min(250, B - a)
        m = rng.multinomial(C, np.full(C, 1.0 / C), size=b).astype(float)
        D1, D2, DA = m @ E1, m @ E2, m @ EA
        nr = np.cumsum(DA[:, ::-1], axis=1)[:, ::-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            h = np.where(nr > 0, (D1 + D2) / nr, 0.0)
            s_after = np.cumprod(1.0 - h, axis=1)
            s_before = np.concatenate([np.ones((b, 1)), s_after[:, :-1]], axis=1)
            f1 = np.where(nr > 0, s_before * D1 / nr, 0.0)[:, keep].sum(axis=1)
            f2 = np.where(nr > 0, s_before * D2 / nr, 0.0)[:, keep].sum(axis=1)
        f1s.append(f1)
        f2s.append(f2)
    f1s, f2s = np.concatenate(f1s), np.concatenate(f2s)
    return (float(np.quantile(f1s, 0.05)), float(np.quantile(f1s, 0.95)), float(np.quantile(f2s, 0.05)), float(np.quantile(f2s, 0.95)))


def km_quantile(time, event, q=0.99):
    """Каплан-Мейер времени до события (event=1), остальное цензура; квантиль q либо None (не достигнут)."""
    time = np.asarray(time)
    event = np.asarray(event)
    o = np.argsort(time, kind="stable")
    t, e = time[o], event[o]
    uq, idx = np.unique(t, return_index=True)
    nr = (len(t) - idx).astype(float)
    d = np.add.reduceat((e == 1).astype(float), idx)
    s = np.cumprod(1.0 - d / nr)
    hit = np.flatnonzero(1.0 - s >= q)
    return float(uq[hit[0]]) if len(hit) else None


def bin_index(x, edges):
    return np.searchsorted(np.asarray(edges), x, side="right") - 1


def gmax_rule(eps, n_units_min=N_UNITS_MIN, B=B_BOOT):
    """eps - список эпизодов класса (EpisodeSim.eps). -> dict: G_max, Gsig_max, A90 по корзинам, T_struct, таблица корзин."""
    if not eps:
        return dict(gmax=None, note="нет эпизодов")
    t = np.array([e[0] for e in eps])
    g = np.array([e[1] for e in eps])
    gs = np.array([e[2] for e in eps])
    out = np.array([e[3] for e in eps])
    dur = np.array([e[4] for e in eps], float)
    adv = np.array([e[5] for e in eps])
    day = np.array([e[6] for e in eps])
    asset = np.array([e[7] for e in eps])
    cluster = np.array(["%s|%d" % (a, d) for a, d in zip(asset, day)])
    res = dict(bins=[], gmax=None, gmax_kind=None, gsig_max=None, a90=[], t_struct=None)
    for name, val, edges in (("bps", g, BINS_BPS), ("sigma", gs, BINS_SIG)):
        bi = bin_index(val, edges)
        rows = []
        found = None
        last_support = None
        for b, lo in enumerate(edges):
            m = bi == b
            if not m.any():
                continue
            units = len(set(cluster[m]))
            row = dict(basis=name, lo=lo, hi=edges[b + 1] if b + 1 < len(edges) else None, episodes=int(m.sum()), units=units, support=units >= n_units_min)
            if row["support"]:
                tt = dur[m]
                ev = np.where(out[m] == 3, 0, out[m])          # (Т) и цензура конца периода - цензура по К/С
                f1, f2 = aj_cif(tt, ev)
                l1, u1, l2, u2 = aj_cluster_bootstrap(tt, ev, cluster[m], B=B)
                row.update(p_conv=f1, p_stop=f2, p_conv_lo=l1, p_conv_hi=u1, p_stop_lo=l2, p_stop_hi=u2, race_lost=bool(u1 < l2))
                last_support = row
                if found is None and u1 < l2:
                    found = row
            rows.append(row)
        res["bins"] += rows
        if name == "bps":
            if found is not None:
                res["gmax"], res["gmax_kind"] = found["lo"], "измеренный разрыв"
            elif last_support is not None:
                res["gmax"] = last_support["hi"] if last_support["hi"] is not None else last_support["lo"] * 2
                res["gmax_kind"] = "граница опоры данных, не измеренный разрыв"
        else:
            if found is not None:
                res["gsig_max"] = found["lo"]
            elif last_support is not None:
                res["gsig_max"] = last_support["hi"] if last_support["hi"] is not None else last_support["lo"] * 2
    if res["gmax"] is None or res["gmax"] <= L.THETA_COST:
        res["empty_range"] = True
    gm = res["gmax"] or math.inf
    bi = bin_index(g, BINS_BPS)
    a90 = []
    for b, lo in enumerate(BINS_BPS):
        m = (bi == b) & (g < gm)
        units = len(set(cluster[m])) if m.any() else 0
        a90.append(float(np.quantile(adv[m], 0.9)) if units >= n_units_min else None)
    res["a90_edges"], res["a90_vals"] = BINS_BPS, a90
    below = g < gm
    if below.any():
        ev1 = (out[below] == 1).astype(int)
        res["t_struct"] = km_quantile(dur[below], ev1, 0.99)
        res["t_struct_truncated"] = res["t_struct"] is None
        if res["t_struct"] is None:
            res["t_struct"] = TMAX
    # K6: был ли стоп прав
    stops = [e for e in eps if e[3] == 2 and len(e) > 10]
    k6 = []
    for j, TT in enumerate((120, 360, 1440)):
        if stops:
            hits = np.array([e[10][j][0] for e in stops], float)
            mx = np.array([e[10][j][1] for e in stops], float)
            k6.append(dict(T=TT, stops=len(stops), share_converged=float(hits.mean()), ext_q50=float(np.nanquantile(mx, 0.5)), ext_q90=float(np.nanquantile(mx, 0.9)),
                           ext_q99=float(np.nanquantile(mx, 0.99))))
    res["k6_stop_was_right"] = k6
    res["episodes_total"] = len(eps)
    return res


# ------------------------------------------------------------------ прочие структурные числа (K4)
def quantile_from_hist(h, q):
    h = np.asarray(h, float)
    c = np.cumsum(h)
    if c[-1] == 0:
        return None
    return int(np.searchsorted(c, q * c[-1]))


def k_stale_from_meta(metas, tag, q=0.99):
    """K_stale(класс) = 0,99-квантиль длин серий пустых минут по ногам; не более T_max."""
    tot = np.zeros(1441)
    for m in metas:
        for leg in ("A", "B"):
            tot += np.array(m["run_hist_%s_%s" % (leg, tag)], float)
    v = quantile_from_hist(tot, q)
    return None if v is None else int(min(max(v, 1), TMAX))


def m_r_from_meta(metas, tag):
    """m_r(класс) = наименьший лаг (в валидных минутах), на котором медиана по парам автокорреляции d_idio < 1/2; не менее 1."""
    acs = [m["acf_" + tag] for m in metas if m.get("acf_" + tag)]
    if not acs:
        return None
    med = np.nanmedian(np.array(acs, float), axis=0)
    below = np.flatnonzero(med < 0.5)
    return int(below[0] + 1) if len(below) else len(med)


def hl15_from_days(days_stats, day_idx, B=B_BOOT, seed=SEED, min_days=3, n_reg_min=300):
    """Полураспад пары: days_stats [сутки, 6] = (n, Σx, Σy, Σxx, Σxy, Σyy) для пар (x_{t-15}, y_t); day_idx - сутки (индексы) для оценки.
    -> dict(n_reg, phi15, hl15, hl_hi, admitted_basis) : верхняя граница - 95 % бутстрепа HL15 по суткам; phi <= 0 в ресэмпле -> HL = 0, phi >= 1 -> inf."""
    st = np.asarray(days_stats, float)[list(day_idx)]
    st = st[st[:, 0] > 0]
    out = dict(n_reg=int(st[:, 0].sum()) if len(st) else 0, days=int(len(st)), phi15=None, hl15=None, hl_hi=None)
    if len(st) < min_days or out["n_reg"] < n_reg_min:
        return out

    def phi_of(s):
        n, sx, sy, sxx, sxy = s[..., 0], s[..., 1], s[..., 2], s[..., 3], s[..., 4]
        with np.errstate(invalid="ignore", divide="ignore"):
            return (n * sxy - sx * sy) / (n * sxx - sx * sx)
    phi = float(phi_of(st.sum(axis=0)))
    out["phi15"] = phi
    if not (0.0 < phi < 1.0):
        return out
    out["hl15"] = 15.0 * math.log(2.0) / -math.log(phi)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(st), size=(B, len(st)))
    ph = phi_of(st[idx].sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        hl = np.where(ph >= 1.0, np.inf, np.where(ph <= 0.0, 0.0, 15.0 * math.log(2.0) / -np.log(np.clip(ph, 1e-12, 1 - 1e-12))))
    hl = np.where(np.isfinite(ph), hl, np.inf)
    out["hl_hi"] = float(np.quantile(hl, 0.95))
    return out


def q5_admitted(q5row, T_min):
    """Допуск пары к комбинации с таймаутом T (минуты): n_reg >= 300, phi15 in (0,1), HL15_верх <= T/4."""
    return q5row is not None and q5row.get("hl_hi") is not None and q5row.get("phi15") is not None and 0 < q5row["phi15"] < 1 and q5row["hl_hi"] <= T_min / 4.0


def c_quantiles(out, cells, Lw, last_idx, q=0.99):
    """q_c(класс) = 0,99-квантиль |c_t - c_{t-60}| по минутам, где в ячейке >= 5 валидных пар (последние last_idx минут кэша - обрезка по конвейеру)."""
    vals = []
    for cell in cells:
        c, cnt = W.load_cell_c(out, cell, Lw)
        c, cnt = c[:last_idx], cnt[:last_idx]
        a = np.abs(c[60:] - c[:-60])
        ok = (cnt[60:] >= 5) & (cnt[:-60] >= 5)
        vals.append(a[ok])
    v = np.concatenate(vals) if vals else np.zeros(0)
    return float(np.quantile(v, q)) if len(v) else None


def shift_m(out, pids, Lw, last_idx, q=0.99, chunk=400):
    """m(класс) = 0,99-квантиль r6 = |b_s - b|/sigma по валидным минутам формирования (по гистограмме, память ограничена)."""
    pdir = W.cache_dirs(out, Lw)[0]
    edges = np.concatenate([np.linspace(0, 20, 20001), [np.inf]])
    h = np.zeros(len(edges) - 1)
    for pid in pids:
        a = np.load(os.path.join(pdir, pid + ".npy"), mmap_mode="r")[2, :last_idx]
        a = np.asarray(a, float)
        a = a[np.isfinite(a)]
        h += np.histogram(a, bins=edges)[0]
    if h.sum() == 0:
        return None
    c = np.cumsum(h)
    i = int(np.searchsorted(c, q * c[-1]))
    return float(edges[min(i + 1, len(edges) - 2)])


def stale_fraction_quantile(metas, tag, q=0.9):
    tot = np.zeros(61)
    for m in metas:
        for leg in ("A", "B"):
            h = m.get("stale_hist_%s_%s" % (leg, tag))
            if h:
                tot += np.array(h, float)
    v = quantile_from_hist(tot, q)
    return None if v is None else v / 60.0


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, default=lambda o: None if (isinstance(o, float) and not math.isfinite(o)) else float(o))
