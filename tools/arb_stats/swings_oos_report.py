"""Таблицы отчёта «качели»: подгонка OU, регулярность, режимы, прогноз вне выборки (с плацебо), топ-30, окна (боковая таблица).

Запуск: python3 swings_oos_report.py <swings.pkl> <fit.pkl> <regime.pkl> <oos.pkl> <выход_каталог> — пишет report.md и top30-N<N>.csv.
Десятичная точка в числах кода; в тексте отчёта заменяется вручную. Единицы — % и качелей/ч.
"""
import csv
import pickle
import sys
from pathlib import Path

import numpy as np
from scipy.special import gammaln
from scipy.stats import rankdata, spearmanr

sys.path.insert(0, str(Path(__file__).parent))
import bocpd  # noqa: E402
import maker_run  # noqa: E402
import swings  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import load_pool, norm  # noqa: E402

NS = swings.NS
FEE_TT = 0.126
FEE_MT, FEE_MM = 0.1071, 0.0882
OUT = []


def p(s=""):
    OUT.append(s)


def pct(a, ps=(10, 50, 90)):
    a = np.array([x for x in a if x is not None and np.isfinite(x)], float)
    return np.percentile(a, ps) if len(a) else np.full(len(ps), np.nan)


def f(x, d=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def grp_of(k):
    return "bx" if "bingx" in k[1:] else "ok"


# ---------------------------------------------------------------- подгонка и регулярность
def fit_tables(F):
    keys = {g: [k for k in F if grp_of(k) == g and "mu" in F[k]] for g in ("ok", "bx")}
    p("### Подгонка OU (минутная сетка) и её проверка")
    p("| группа | пар | σ середины, % (p10/p50/p90) | φ за 1 мин | полураспад AR(1), мин | остатки AR(1): Льюнг–Бокс p < 0,01 | остатки AR(2): p < 0,01 | BIC выбирает AR(2) (Δ > 10) |")
    p("|---|---|---|---|---|---|---|---|")
    for g, ks in keys.items():
        sd, ph, hl = pct([F[k]["sd"] for k in ks]), pct([F[k]["phi1"] for k in ks]), pct([F[k]["hl_min"] for k in ks])
        p(f"| {g} | {len(ks)} | {f(sd[0], 3)} / {f(sd[1], 3)} / {f(sd[2], 3)} | {f(ph[0])} / {f(ph[1])} / {f(ph[2])} | {f(hl[0])} / {f(hl[1])} / {f(hl[2])} | "
          f"{np.mean([F[k]['lb1'] < 0.01 for k in ks]) * 100:.1f} % | {np.mean([F[k]['lb2'] < 0.01 for k in ks]) * 100:.1f} % | {np.mean([F[k]['dbic'] > 10 for k in ks]) * 100:.1f} % |")
    p()
    p("Полураспад (с), посчитанный по автокорреляции на шаге Δ (у одного OU не зависит от Δ), медиана [p10; p90]:")
    p("| группа | Δ = 1 с | 10 с | 60 с | 600 с | 3600 с |")
    p("|---|---|---|---|---|---|")
    for g, ks in keys.items():
        row = []
        for d in (1, 10, 60, 600, 3600):
            q = pct([F[k].get("hl_acf", {}).get(d) for k in ks])
            row.append(f"{f(q[1], 0)} [{f(q[0], 0)}; {f(q[2], 0)}]")
        p(f"| {g} | " + " | ".join(row) + " |")
    p()
    p("Автокорреляция (16 лагов 1 с … 6 ч): одна экспонента против двух (медианы по парам):")
    p("| группа | T одной, с | T1, с | T2, с | вес T1 | вес T2 | SSE(2)/SSE(1), медиана | доля пар, где SSE(2) < ½ SSE(1) |")
    p("|---|---|---|---|---|---|---|---|")
    for g, ks in keys.items():
        tw = [F[k]["two"] for k in ks if F[k].get("two")]
        r = np.array([t[2] / t[0] if t[0] > 0 else np.nan for t in tw])
        m = lambda i: f(np.nanmedian([t[i] for t in tw]), 3 if i in (5, 6) else 0)  # noqa: E731
        p(f"| {g} | {m(1)} | {m(3)} | {m(4)} | {m(5)} | {m(6)} | {f(np.nanmedian(r), 3)} | {np.nanmean(r < 0.5) * 100:.1f} % |")
    p()
    p("Частота качелей по OU (минутные μ, σ, φ) против измеренной (L = 300 мс), качелей/ч:")
    p("| группа | N, % | пар с качелями | медиана измеренной среди них | медиана OU среди них | медиана отношения измер./OU | Спирмен (по всем парам) | пар, где измеренная в пределах ×3 от OU |")
    p("|---|---|---|---|---|---|---|---|")
    for g, ks in keys.items():
        for iN, N in enumerate(NS):
            me = np.array([F[k]["rate"][(iN, 300)] for k in ks])
            ou = np.array([F[k]["ou"][iN] for k in ks])
            a = me > 0
            rat = me[a & (ou > 0)] / ou[a & (ou > 0)]
            sp = spearmanr(me, ou)[0] if a.sum() > 5 else np.nan
            inn = np.mean((rat > 1 / 3) & (rat < 3)) * 100 if len(rat) else np.nan
            p(f"| {g} | {N} | {a.sum()} | {f(np.median(me[a]), 3) if a.any() else '—'} | {f(np.median(ou[a]), 4) if a.any() else '—'} | {f(np.median(rat), 1) if len(rat) else '—'} | {f(sp)} | {f(inn, 1)} % |")
    p()


def regularity(F, S, lam_sw):
    p("### Регулярность: время между качелями (L = 300 мс)")
    p("CV = σ / среднее интервалов между закрытиями подряд идущих качелей пары (≥ 10 интервалов); пуассоновский поток — CV ≈ 1, ритмичный — CV ≪ 1. «CV в режиме» — интервалы, нормированные на среднее своего режима потока (BOCPD по интервалам).")
    p("| группа | N, % | пар с ≥ 10 интервалов | CV: p10 / p50 / p90 | доля CV < 0,7 | доля CV > 1,5 | CV в режиме: p10 / p50 / p90 |")
    p("|---|---|---|---|---|---|---|")
    for g in ("ok", "bx"):
        for iN, N in enumerate(NS):
            ks = [k for k in F if grp_of(k) == g and "mu" in F[k] and np.isfinite(F[k]["cv"][(iN, 300)])]
            cv = np.array([F[k]["cv"][(iN, 300)] for k in ks])
            wr = []
            for k in ks:
                tc = np.sort(S[k]["sw"][(iN, 300)][0])
                tau = np.diff(tc) / 1000.0
                tau = tau[tau > 0]
                if len(tau) < 10:
                    continue
                rl = bocpd.bocpd_exp(tau, 1.0 / lam_sw)[0]
                st = np.r_[bocpd.regimes_from_rl(rl, 3, 3), len(tau)]
                z = np.concatenate([tau[a:b] / tau[a:b].mean() for a, b in zip(st[:-1], st[1:]) if b - a >= 5] or [np.zeros(0)])
                if len(z) >= 10:
                    wr.append(z.std())
            q, w = pct(cv), pct(wr)
            if len(cv):
                p(f"| {g} | {N} | {len(cv)} | {f(q[0])} / {f(q[1])} / {f(q[2])} | {np.mean(cv < 0.7) * 100:.0f} % | {np.mean(cv > 1.5) * 100:.0f} % | {f(w[0])} / {f(w[1])} / {f(w[2])} ({len(wr)}) |")
    p()


def regime_tables(R):
    p("### Режимы середины (BOCPD с авторегрессией)")
    he = R["hazard_evidence"]
    p(f"Выбор вероятности смены (150 пар, суммарное лог-правдоподобие данных): " + ", ".join(f"λ = {k if k < 10 ** 8 else '∞'} мин: {v:.0f}" for k, v in sorted(he.items())) + f"; выбрано λ = {R['lam']} мин.")
    p()
    p("| группа | пар | режимов на пару, медиана (p10/p90) | длина режима, ч: p10 / p25 / p50 / p75 / p90 | пар с одним режимом на всё окно | то же на фаз-рандомизованном ряду: режимов на пару / длина p50, ч |")
    p("|---|---|---|---|---|---|")
    for g in ("ok", "bx"):
        ks = [k for k in R["pairs"] if grp_of(k) == g]
        for tag in (None,):
            L, n1, Ls, ns = [], [], [], []
            for k in ks:
                st = bocpd.regimes_from_rl(R["pairs"][k]["rl"])
                n1.append(len(st))
                L += list(np.diff(np.r_[st, len(R["pairs"][k]["rl"])]) / 60.0)
                ss = bocpd.regimes_from_rl(R["pairs"][k]["rl_s"])
                ns.append(len(ss))
                Ls += list(np.diff(np.r_[ss, len(R["pairs"][k]["rl_s"])]) / 60.0)
            q = np.percentile(L, [10, 25, 50, 75, 90])
            nq = np.percentile(n1, [10, 50, 90])
            p(f"| {g} | {len(ks)} | {nq[1]:.0f} ({nq[0]:.0f} / {nq[2]:.0f}) | {q[0]:.1f} / {q[1]:.1f} / {q[2]:.1f} / {q[3]:.1f} / {q[4]:.1f} | {np.mean(np.array(n1) == 1) * 100:.0f} % | "
              f"{np.median(ns):.0f} / {np.median(Ls):.1f} |")
    p()


# ---------------------------------------------------------------- прогноз вне выборки
def stack(O, keys):
    P = O["pred"]
    first = O["oos"][keys[0]]
    sh = first["pred"]["glob"].shape
    pr = {q: np.stack([O["oos"][k]["pred"][q] for k in keys]) for q in P}
    real = {h: np.stack([O["oos"][k]["real"][h] for k in keys]) for h in O["hs"]}
    tn = np.stack([O["oos"][k]["tn"] for k in keys])
    obs = np.stack([O["oos"][k]["obs"] for k in keys])
    hist = np.stack([O["oos"][k]["hist"] for k in keys])
    return pr, real, tn, obs, hist, sh


def score(pr, y, h, tn, obs, mask, perm=None):
    """pr, y, tn, obs: (пар, N, t). mask — (пар, N, t) допустимые. -> (Спирмен ср. по t, NLL Пуассона, лог-оценка времени до следующей)."""
    ok = mask & np.isfinite(pr)
    pr2 = pr if perm is None else perm
    mu = np.maximum(pr2 * h, 0.01)
    yy = np.where(np.isfinite(y), y, 0)
    v = ok & np.isfinite(y)
    nll = float((mu - yy * np.log(mu) + gammaln(yy + 1))[v].mean()) if v.any() else np.nan
    R = np.maximum(pr2, 1e-3) / 3600.0
    ts = np.where(obs, np.log(R) - R * tn, -R * tn)
    tsc = float(ts[ok].mean()) if ok.any() else np.nan
    rs = []
    for o in range(pr.shape[2]):
        m = v[:, o]
        if m.sum() >= 15 and np.ptp(pr2[m, o]) > 0 and np.ptp(yy[m, o]) > 0:
            rs.append(spearmanr(pr2[m, o], yy[m, o])[0])
    return (float(np.mean(rs)) if rs else np.nan), nll, tsc, int(v.sum())


def shuffle_pairs(pr, mask, rng):
    out = pr.copy()
    for iN in range(pr.shape[1]):
        for o in range(pr.shape[2]):
            m = np.flatnonzero(mask[:, iN, o] & np.isfinite(pr[:, iN, o]))
            out[m, iN, o] = pr[rng.permutation(m), iN, o]
    return out


def shift_time(y, tn, obs, rng, min_shift=24):
    n = y.shape[2]
    y2, t2, o2 = y.copy(), tn.copy(), obs.copy()
    for i in range(y.shape[0]):
        s = int(rng.integers(min_shift, n - min_shift))
        y2[i], t2[i], o2[i] = np.roll(y[i], s, axis=-1), np.roll(tn[i], s, axis=-1), np.roll(obs[i], s, axis=-1)
    return y2, t2, o2


def oos_tables(O, keys):
    pr, real, tn, obs, hist, sh = stack(O, keys)
    grp = np.array([grp_of(k) for k in keys])
    ok = np.stack([O["oos"][k]["ok"] for k in keys])[:, None, :] & np.ones((1, len(NS), 1), bool)
    rng = np.random.default_rng(3)
    p("### Прогноз вне выборки")
    p(f"Точки отсчёта — каждые 30 мин с 8-го часа окна ({ok.shape[2]} точек); параметры режима — только по данным до t. «Активные» — пары, у которых до t уже ≥ 3 качелей этого N (отбор по прошлому). "
      f"Спирмен — по парам в каждой точке, среднее по точкам; ΔNLL — выигрыш по логарифмическому правдоподобию Пуассона числа качелей против предиктора glob (нат/наблюдение, > 0 — лучше); "
      f"Δвремя — то же для времени до следующей качели (экспонента с этой частотой, с цензурой на конце данных). Плацебо: «пары» — прогнозы тех же точек перетасованы между парами; «время» — исход взят из другого момента той же пары (сдвиг ≥ 12 ч).")
    res = {}
    for g in ("ok", "bx"):
        gm = (grp == g)[:, None, None]
        for iN in (0, 1, 2, 3):
            N = NS[iN]
            for sub in ("active",):
                mask = ok & gm & (hist >= 3)
                mk = np.zeros_like(mask)
                mk[:, iN, :] = mask[:, iN, :]
                if mk.sum() < 200:
                    continue
                rows = []
                base = {h: score(pr["glob"], real[h], h, tn, obs, mk) for h in O["hs"]}
                for q in O["pred"]:
                    sc = {h: score(pr[q], real[h], h, tn, obs, mk) for h in O["hs"]}
                    rows.append((q, sc))
                for tag in ("пары", "время"):
                    sc = {}
                    for h in O["hs"]:
                        if tag == "пары":
                            sc[h] = score(pr["reg"], real[h], h, tn, obs, mk, perm=shuffle_pairs(pr["reg"], mk, rng))
                        else:
                            y2, t2, o2 = shift_time(real[h], tn, obs, rng)
                            sc[h] = score(pr["reg"], y2, h, t2, o2, mk)
                    rows.append(("reg, плацебо «" + tag + "»", sc))
                res[(g, iN)] = (rows, base, int(mk[:, iN, :].sum()), int((mk[:, iN, :].any(axis=1)).sum()))
                p(f"**{g}, N = {N} %, активные: {res[(g, iN)][3]} пар, {res[(g, iN)][2]} наблюдений**")
                p("| предиктор | Спирмен 1 ч | Спирмен 6 ч | ΔNLL 1 ч | ΔNLL 6 ч | Δвремя |")
                p("|---|---|---|---|---|---|")
                for q, sc in rows:
                    p(f"| {q} | {f(sc[1][0])} | {f(sc[6][0])} | {f(base[1][1] - sc[1][1], 3)} | {f(base[6][1] - sc[6][1], 3)} | {f(sc[1][2] - base[1][2], 3)} |")
                p()
    return pr, real, tn, obs, hist, grp, ok


_BOOKS = {}
BWORK = "/data/swings/b/parsed"


def depth_med(D, k, iN):
    """Глубина кругов пары: $ на лучшем уровне (меньше из двух ног) на входе и выходе круга, минимум из двух; медиана и p10 по качелям пары (L = 300)."""
    coin, a, b = k
    tc, dur, gr, side = D["pairs"][k]["sw"][(iN, 300)]
    if not len(tc):
        return None, None
    if "meta" not in _BOOKS:
        day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(D["t1"] / 1000))
        _BOOKS["meta"] = {ex: load_pool(ex, symmap_for(day)[1])[1] for ex in ("binance", "bybit", "okx", "bingx")}
    bk = []
    for ex in (a, b):
        key = (coin, ex)
        if key not in _BOOKS:
            sym = D["info"][coin][ex]["sym"]
            mult = norm(ex, sym, _BOOKS["meta"][ex])[1]
            unit = float((_BOOKS["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
            _BOOKS[key] = maker_run.load_book(BWORK, ex, sym, mult, unit)
        bk.append(_BOOKS[key])
    A, B = bk
    t_exit, t_entry = D["t0"] + tc.astype(np.int64), D["t0"] + tc.astype(np.int64) - dur - 0
    # исполнение: вход — через L после пересечения входа (= t_закрытия − длительность), выход — t_закрытия
    def d(t, sd):
        ia, ib = np.searchsorted(A["e"], t, "right") - 1, np.searchsorted(B["e"], t, "right") - 1
        sell = np.minimum(A["dbid"][ia], B["dask"][ib])      # Sell: продать A по биду, купить B по оферу
        buy = np.minimum(A["dask"][ia], B["dbid"][ib])
        return np.where(sd > 0, sell, buy)
    dep = np.minimum(d(t_entry, side), d(t_exit, -side))
    return float(np.median(dep)), float(np.percentile(dep, 10))


def top30(O, keys, S, pr, real, grp, hist, D=None):
    p("### Топ-30 пар по подтверждённым вне выборки качелям")
    p("Правило отбора (задано заранее): на часовой точке t пара «отобрана», если режимный предиктор reg даёт ≥ 1 качели в ближайший час и у неё уже ≥ 3 качелей этого N. Подтверждено — реальные качели в (t, t + 1 ч] "
      "по отобранным точкам (непересекающиеся часы). Ранжирование по числу подтверждённых качелей — **найдено на этих данных** (отбор по исходу); рядом проверка без заглядывания вперёд: ранг по первой половине точек, счёт по второй.")
    O1 = slice(0, None, 2)
    npts = pr["reg"].shape[2]
    for iN, N in enumerate(NS):
        sel = (pr["reg"][:, iN, O1] >= 1.0) & (hist[:, iN, O1] >= 3) & np.isfinite(real[1][:, iN, O1])
        y = np.where(sel, real[1][:, iN, O1], 0)
        half = y.shape[1] // 2
        tot = y.sum(1)
        order = np.argsort(-tot)[:30]
        # без заглядывания: ранг по первой половине, счёт по второй
        ya, yb = y[:, :half].sum(1), y[:, half:].sum(1)
        sa, sb = sel[:, :half].sum(1), sel[:, half:].sum(1)
        topa = np.argsort(-ya)[:30]
        topa = topa[ya[topa] > 0]
        p(f"**N = {N} %** — отобранных пар: {int((sel.any(1)).sum())}; всего подтверждено качелей: {int(tot.sum())}; без заглядывания (топ по 1-й половине, счёт по 2-й): "
          f"{len(topa)} пар, на отобранный час {yb[topa].sum() / max(sb[topa].sum(), 1):.2f} качелей, по всем отобранным во 2-й половине {yb.sum() / max(sb.sum(), 1):.2f}")
        rows = []
        for i in order:
            if tot[i] <= 0:
                break
            k = keys[i]
            tc, dur, gr, side = S[k]["sw"][(iN, 300)]
            to = O["oos"][k]["to"][O1]
            gs = []
            for o in np.flatnonzero(sel[i]):
                a = to[o] - O["t0"]
                m = (tc > a) & (tc <= a + swings.HOUR)
                gs += list(gr[m])
            gs = np.array(gs)
            dm, d10 = depth_med(D, k, iN) if D is not None else (None, None)
            rows.append([k[0], f"{k[1]}→{k[2]}", grp[i], int(sel[i].sum()), int(tot[i]), f"{(y[i][sel[i]] > 0).mean():.2f}", f(np.median(gs), 3) if len(gs) else "—",
                         f(np.median(gs) - FEE_TT, 3) if len(gs) else "—", f(np.median(gs) - FEE_MT, 3) if len(gs) else "—", f(np.median(gs) - FEE_MM, 3) if len(gs) else "—",
                         f(dm, 0), f(d10, 0)])
        with open(Path(OUTDIR, f"top30-N{N}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["монета", "пара", "группа", "отобранных часов", "подтверждённых качелей", "доля отобранных часов с ≥ 1 качелей", "валовый итог круга, медиана %", "чистый, 4 тейкера %", "чистый, мейкер+тейкер (закрытие тейкером) %", "чистый, мейкер+тейкер (закрытие мейкером) %", "глубина круга, $ (медиана)", "глубина, $ (p10)"])
            w.writerows(rows)
        p("| монета | пара | гр. | отобр. часов | подтв. качелей | доля часов с ≥ 1 | валовый круг, % (мед.) | чистый 4 тейкера | чистый м+т (т) | чистый м+т (м) | глубина, $ (мед.) | глубина p10 |")
        p("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows[:30]:
            p("| " + " | ".join(str(x) for x in r) + " |")
        p()


def stream_tables(S, t0, t1):
    p("### Поток качелей по парам (всё окно)")
    hours = (t1 - t0) / swings.HOUR
    p(f"Окно {hours:.1f} ч. Качели подтверждены на t + L (L = 0 — сырые пересечения). «Доля времени за ±N» — Sell ≥ +N или Buy ≤ −N, по парам (по часовым корзинам, пропуски сбора вычтены).")
    p("| группа | N, % | качелей L = 0 / 50 / 300 мс (всего) | пар с ≥ 1 качелей (L = 300) | медиана длительности качели, с (пул L = 300) | медиана по парам медиан, с | доля времени за ±N: p50 / p90 / макс по парам, % |")
    p("|---|---|---|---|---|---|---|")
    for g in ("ok", "bx"):
        ks = [k for k in S if grp_of(k) == g]
        for iN, N in enumerate(NS):
            n3 = [sum(len(S[k]["sw"][(iN, L)][0]) for k in ks) for L in swings.LS]
            act = [k for k in ks if len(S[k]["sw"][(iN, 300)][0]) >= 1]
            durs = np.concatenate([S[k]["sw"][(iN, 300)][1] for k in act]) / 1000.0 if act else np.zeros(0)
            pm = [np.median(S[k]["sw"][(iN, 300)][1]) / 1000.0 for k in act]
            sh = np.array([S[k]["bey"][iN].sum() / max(S[k]["cov"].sum(), 1) * 100 for k in ks])
            p(f"| {g} | {N} | {n3[0]} / {n3[1]} / {n3[2]} | {len(act)} из {len(ks)} | {f(np.median(durs), 1) if len(durs) else '—'} | {f(np.median(pm), 1) if pm else '—'} | "
              f"{f(np.percentile(sh, 50), 3)} / {f(np.percentile(sh, 90), 3)} / {f(sh.max(), 2)} |")
    p()


def swing_regimes(S, lam_sw):
    p("### Режимы потока качелей (BOCPD по интервалам, L = 300 мс; пары с ≥ 30 качелей)")
    p("| группа | N, % | пар | режимов на пару: p50 (p10/p90) | длительность режима, ч: p25 / p50 / p75 | качелей в режиме: p25 / p50 / p75 | отношение частот соседних режимов: p50 / p90 |")
    p("|---|---|---|---|---|---|---|")
    for g in ("ok", "bx"):
        for iN, N in enumerate(NS):
            ks = [k for k in S if grp_of(k) == g and len(S[k]["sw"][(iN, 300)][0]) >= 30]
            if not ks:
                continue
            nr, dh, cn, ratio = [], [], [], []
            for k in ks:
                tc = np.sort(S[k]["sw"][(iN, 300)][0]) / 1000.0
                tau = np.diff(np.r_[max(S[k]["t0e"], 0) / 1000.0, tc])
                tau = np.maximum(tau, 1e-3)
                rl = bocpd.bocpd_exp(tau, 1.0 / lam_sw)[0]
                st = np.r_[bocpd.regimes_from_rl(rl, 3, 3), len(tau)]
                nr.append(len(st) - 1)
                A = np.r_[max(S[k]["t0e"], 0) / 1000.0, tc]
                rates = []
                for a, b in zip(st[:-1], st[1:]):
                    dh.append((A[b] - A[a]) / 3600.0)
                    cn.append(b - a)
                    rates.append((b - a) / max(A[b] - A[a], 1e-3))
                ratio += [max(x / y, y / x) for x, y in zip(rates[:-1], rates[1:])]
            q, d, c = np.percentile(nr, [10, 50, 90]), np.percentile(dh, [25, 50, 75]), np.percentile(cn, [25, 50, 75])
            rr = np.percentile(ratio, [50, 90]) if ratio else [np.nan, np.nan]
            p(f"| {g} | {N} | {len(ks)} | {q[1]:.0f} ({q[0]:.0f}/{q[2]:.0f}) | {d[0]:.2f} / {d[1]:.2f} / {d[2]:.2f} | {c[0]:.0f} / {c[1]:.0f} / {c[2]:.0f} | {f(rr[0], 1)} / {f(rr[1], 1)} |")
    p()


def windows_side(S):
    p("### Боковая таблица: фиксированные окна (прежний подход)")
    p("Число качелей (L = 300 мс, по времени закрытия) в окнах 1 / 6 / 12 / 24 ч подряд от начала данных; Спирмен между окном k и k + 1 по парам (среднее по переходам), доля «≥ 2 → ≥ 2» среди пар с ≥ 2 в окне k; плацебо — окно k + 1 перетасовано между парами.")
    p("| группа | N, % | окно | переходов | Спирмен | ≥ 2 → ≥ 2 (доля) | базовая доля ≥ 2 в k + 1 | плацебо Спирмен |")
    p("|---|---|---|---|---|---|---|---|")
    keys = sorted(S)
    rng = np.random.default_rng(9)
    t0, t1 = S["_t0"], S["_t1"]
    span = (t1 - t0) / swings.HOUR
    for g in ("ok", "bx"):
        ks = [k for k in keys if k != "_t0" and k != "_t1" and grp_of(k) == g]
        for iN in (1, 2):
            tcs = [S[k]["sw"][(iN, 300)][0] for k in ks]
            for W in (1, 6, 12, 24):
                nw = int(span // W)
                C = np.stack([np.bincount(np.minimum((tc // (W * swings.HOUR)).astype(int), nw), minlength=nw + 1)[:nw] for tc in tcs])
                rs, rp, hit, base = [], [], [], []
                for k in range(nw - 1):
                    a, b = C[:, k], C[:, k + 1]
                    if np.ptp(a) > 0 and np.ptp(b) > 0:
                        rs.append(spearmanr(a, b)[0])
                        rp.append(spearmanr(a, rng.permutation(b))[0])
                    m = a >= 2
                    if m.sum() >= 3:
                        hit.append((b[m] >= 2).mean())
                        base.append((b >= 2).mean())
                p(f"| {g} | {NS[iN]} | {W} ч | {nw - 1} | {f(np.mean(rs)) if rs else '—'} | {f(np.mean(hit)) if hit else '—'} | {f(np.mean(base)) if base else '—'} | {f(np.mean(rp)) if rp else '—'} |")
    p()


OUTDIR = "."


def main(sw, fit, reg, oos, outdir):
    global OUTDIR
    OUTDIR = outdir
    Path(outdir).mkdir(parents=True, exist_ok=True)
    D = pickle.load(open(sw, "rb"))
    S = D["pairs"]
    F = pickle.load(open(fit, "rb"))["fit"]
    R = pickle.load(open(reg, "rb"))
    O = pickle.load(open(oos, "rb"))
    keys = sorted(O["oos"])
    stream_tables(S, D["t0"], D["t1"])
    fit_tables(F)
    regularity(F, S, O["lam_sw"])
    swing_regimes(S, O["lam_sw"])
    regime_tables(R)
    pr, real, tn, obs, hist, grp, ok = oos_tables(O, keys)
    top30(O | {"t0": D["t0"]}, keys, S, pr, real, grp, hist, D)
    S2 = dict(S)
    S2["_t0"], S2["_t1"] = D["t0"], D["t1"]
    windows_side(S2)
    Path(outdir, "report.md").write_text("\n".join(OUT), encoding="utf-8")
    print("\n".join(OUT))


if __name__ == "__main__":
    main(*sys.argv[1:6])
