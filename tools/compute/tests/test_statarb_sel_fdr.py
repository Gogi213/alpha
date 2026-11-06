"""TK-493 S1/S2: оценка доли ложных A1 по эмпирическому нулю в единицах баз (sel_fdrlib), сдвиги и выравнивания нуля (sel_shift), синтетический нуль.
S2 (П-37 r3.2, D13): нуль на РЁБРАХ реального графа (9 повторов, 12 выравниваний на ребро): тесты выравниваний, нуль-мир и мир с сигналом на уровне статистик
(SyntheticNull) и СКВОЗНОЙ на ценах (EndToEnd: случайные блуждания, EG на окне, те же смещения серий, что в sel_fdr.py).
Запуск: python -m unittest tools/compute/tests/test_statarb_sel_fdr.py (из корня дерева); сквозная синтетика считает ~1 мин - на Deck (~/xdl9/venv/bin/python)."""
import itertools, os, sys, unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "statarb", "xdl9"))
import sel_fdrlib as F  # noqa: E402
import sel_shift as SH  # noqa: E402


class Greedy(unittest.TestCase):
    def test_star_counts_once(self):
        # символ 0 образует 50 пар с очень низкой статистикой; остальные пары выше: принята ровно одна пара звезды
        lo = [0] * 50 + [1, 3]
        hi = list(range(1, 51)) + [2, 4]
        t = [-9.0 + 0.01 * i for i in range(50)] + [-3.0, -2.5]
        acc = F.greedy_matching(lo, hi, t, nsym=51)
        self.assertEqual(len(acc), 2)                      # (0,1) звезда; (1,2) занята базой 1; (3,4) свободна
        self.assertEqual(sorted(np.asarray(t)[acc])[0], -9.0)
        pair_in_star = [i for i in acc if lo[i] == 0]
        self.assertEqual(len(pair_in_star), 1)

    def test_prefix_property(self):
        rng = np.random.RandomState(1)
        n = 40
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        lo = np.array([p[0] for p in pairs]); hi = np.array([p[1] for p in pairs])
        t = rng.randn(len(pairs))
        full = F.greedy_matching(lo, hi, t, nsym=n)
        for thr in (-1.5, -0.5, 0.3):
            m = t <= thr
            sub = F.greedy_matching(lo[m], hi[m], t[m], nsym=n)
            self.assertEqual(sorted(np.asarray(t)[full][np.asarray(t)[full] <= thr].tolist()), sorted(t[m][sub].tolist()))

    def test_tie_order_by_pair(self):
        acc = F.greedy_matching([2, 0, 0], [3, 1, 2], [-1.0, -1.0, -1.0], nsym=4)
        self.assertEqual(sorted(acc.tolist()), [0, 1])     # (0,1) раньше (0,2) раньше (2,3); (0,1) и (2,3) вместе
        self.assertEqual(acc.tolist()[0], 1)

    def test_orientation_irrelevant(self):
        a = F.greedy_matching([0, 1], [1, 2], [-2.0, -3.0], nsym=3)
        b = F.greedy_matching([1, 2], [0, 1], [-2.0, -3.0], nsym=3)
        self.assertEqual(a.tolist(), b.tolist())

    def test_matching_size_at_most_half(self):
        rng = np.random.RandomState(2)
        n = 31
        pairs = list(itertools.combinations(range(n), 2))
        lo = np.array([p[0] for p in pairs]); hi = np.array([p[1] for p in pairs])
        acc = F.greedy_matching(lo, hi, rng.randn(len(pairs)), nsym=n)
        self.assertLessEqual(len(acc), n // 2)
        self.assertGreaterEqual(len(acc), n // 4)


class Curve(unittest.TestCase):
    def test_q_monotone_and_tstar(self):
        real = np.array([-6.0, -5.5, -5.0, -4.0, -3.0, -2.0] + [-1.0] * 6)
        null = np.array(sorted([-5.2, -4.5, -4.4, -4.3, -3.9, -3.5, -3.0, -2.5, -2.4, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0]))
        k0, R, fdr, q = F.fdr_curve(real[np.argsort(real)], null)
        self.assertTrue(np.all(np.diff(q) >= -1e-15))
        self.assertTrue(np.all(q <= fdr + 1e-15))

    def test_admission_k0_min(self):
        # реальные: много сильных; нулевых мало -> FDR мал при k0 < 5: «отбор не выполнен»
        real = np.array([-8.0] * 20 + [-1.0] * 20)
        null = np.array([-8.0 + 0.0] * 0 + [-7.0, -0.5] + [0.0] * 40)
        r = F.select_threshold(real, null, q_level=0.10, k0_min=5)
        self.assertFalse(r["ok"])
        self.assertTrue(r["status"].startswith("отбор не выполнен"), r["status"])
        r2 = F.select_threshold(real, null, q_level=0.10, k0_min=1)
        self.assertTrue(r2["ok"], r2)

    def test_no_selection(self):
        rng = np.random.RandomState(3)
        real = rng.randn(60); null = rng.randn(500)
        r = F.select_threshold(real, null)
        self.assertFalse(r["ok"])
        self.assertTrue(r["status"].startswith("нет отбора"), r["status"])

    def test_too_few(self):
        r = F.select_threshold([-3.0] * 5, [0.0] * 50)
        self.assertTrue(r["status"].startswith("нет отбора: мало пар"))
        self.assertEqual(r["kind"], "few")

    def test_report_kinds_strong_vs_none(self):
        # r3.2 D13: «отбор не выполнен: сильные пары есть (R_base, k0_base)» отличается от «пар нет»; R_base и k0_base печатаются в обоих случаях
        real = np.array([-8.0] * 20 + [-1.0] * 20)
        null = np.array([-7.0, -0.5] + [0.0] * 40)
        r = F.select_threshold(real, null)
        self.assertEqual(r["kind"], "strong")
        self.assertIn("сильные пары есть", r["status"])
        self.assertIn("R_base = %d" % r["R"], r["status"])
        self.assertIn("k0_base = %d" % r["k0"], r["status"])
        self.assertGreater(r["R"], 0)
        self.assertAlmostEqual(r["fdr_hi"], min(1.0, 3 * r["fdr"]))
        rng = np.random.RandomState(5)
        r2 = F.select_threshold(rng.randn(60), rng.randn(500))
        self.assertEqual(r2["kind"], "none")
        self.assertIn("пар нет", r2["status"])
        self.assertIn("R_base", r2["status"])



class Shifts(unittest.TestCase):
    def test_windows(self):
        for mode in ("cal", "trade"):
            a, b = SH.common_window(mode)
            self.assertEqual((a, b), (2467, 2478))
            days, wd, ok = SH.check_span(mode)
            self.assertEqual((days, wd, ok), (11, 7, True))
            self.assertEqual(SH.form_args(mode), dict(from_day=2478, to_day=2479, window_days=11))

    def test_trade_day_f7_rule(self):
        # k = +1 совпадает с правилом F7 (formb): пн <- пт, сб и вс <- пт, иначе предыдущие сутки
        for d in range(2466, 2479):
            dow = (d + 2) % 7
            want = d - 3 if dow == 0 else (d - 1 if dow == 6 else d - 1)
            if dow == 5:
                want = d - 1             # суббота <- пятница
            if dow == 6:
                want = d - 2             # воскресенье <- пятница
            self.assertEqual(SH.source_day(d, 1, "trade"), want, (d, dow))

    def test_sources_stay_in_formation(self):
        for mode in ("cal", "trade"):
            for k in SH.SHIFTS:
                for d in SH.common_days(mode):
                    s = SH.source_day(d, k, mode)
                    self.assertTrue(SH.D_FORM0 <= s < SH.D_TRADE0)
        self.assertIsNone(SH.source_day(SH.D_TRADE0, 1, "cal"))     # торговое окно как цель не существует
        self.assertIsNone(SH.source_day(2478, -1, "cal"))           # сдвиг -1 требует суток 15.10


class Alignments(unittest.TestCase):
    """Нуль на рёбрах реального графа (r3.2, D13): 12 выравниваний на ребро, у каждого ребра своя перестановка, точных копий статистик нет."""

    def test_constants(self):
        self.assertEqual(len(SH.RUNS), 6)
        self.assertEqual(SH.N_ALIGN, 12)
        self.assertEqual(SH.N_TRAIN, 9)
        self.assertEqual({o for r in SH.RUNS for o in r}, set(SH.OFFSETS))
        self.assertEqual(set(SH.OFFSETS) - {"r"}, {SH.TAG[k] for k in SH.SHIFTS})          # смещения = теги сдвигов {+1, +2, -1} плюс реальная серия
        self.assertEqual(len({SH.run_dir(i) for i in range(6)}), 6)
        self.assertEqual(SH.run_dir(0), "al_p2_p1")

    def test_all_offsets_inside_formation(self):
        # источники всех четырёх окон лежат внутри формирования 01.10-14.10 (сутки 2465-2478): окно EG 2467-2477, смещения {-2, -1, 0, +1}
        a, b = SH.common_window("cal")
        for o in (-2, -1, 0, 1):
            self.assertGreaterEqual(a + o, SH.D_FORM0)
            self.assertLessEqual(b + o, SH.D_TRADE0)
        for k, tag in SH.TAG.items():
            self.assertEqual(SH.source_day(a, k, "cal") - a, -k)                          # сдвиг +k берёт источник на k суток раньше

    def test_perm_per_edge(self):
        perm = F.draw_alignments(500)
        self.assertEqual(perm.shape, (500, 12))
        for row in perm[:50]:
            self.assertEqual(sorted(row.tolist()), list(range(12)))
        self.assertGreater(len({tuple(r[:9]) for r in perm}), 450)                        # у рёбер разные порядки
        self.assertTrue(np.array_equal(perm, F.draw_alignments(500)))                    # детерминированно (seed 37)
        self.assertFalse(np.array_equal(perm[:100], F.draw_alignments(500, seed=38)[:100]))

    def test_repeat_stats_picks_same_pair(self):
        # TT[run, a, b] кодирует (run, a, b): каждая нулевая статистика ребра (lo, hi) принадлежит ИМЕННО этой паре активов и разным выравниваниям в разных повторах
        ns = 7
        TT = np.zeros((6, ns, ns))
        for r in range(6):
            for a in range(ns):
                for b in range(ns):
                    TT[r, a, b] = 10000 * r + 100 * a + b
        lo = np.array([0, 1, 2, 5]); hi = np.array([3, 4, 6, 6])
        perm = F.draw_alignments(len(lo))
        seen = [set() for _ in lo]
        for j in range(12):
            t = F.repeat_stats(TT, lo, hi, perm, j)
            for e, v in enumerate(t):
                r, a, b = int(v) // 10000, (int(v) // 100) % 100, int(v) % 100
                self.assertEqual({a, b}, {lo[e], hi[e]})                                   # та же пара активов
                self.assertNotIn((r, a), seen[e])                                          # выравнивание ребра не повторяется
                seen[e].add((r, a))
        self.assertTrue(all(len(x) == 12 for x in seen))                                   # за 12 повторов - все 12 различных выравниваний

    def test_repeat_matchings_drop_nan(self):
        ns = 5
        TT = np.full((6, ns, ns), np.nan)
        TT[:, 0, 1] = -5.0; TT[:, 1, 0] = -5.0
        TT[:, 2, 3] = -4.0; TT[:, 3, 2] = -4.0
        lo = np.array([0, 2, 0]); hi = np.array([1, 3, 4])
        out = F.repeat_matchings(TT, lo, hi, F.draw_alignments(3), [0, 1], ns)
        for l, h, t, idx in out:
            self.assertEqual(len(t), 2)                                                    # ребро (0, 4) без статистики выпало из повтора
            self.assertEqual(len(idx), 2)


def sim_catalog(rng, nsym, n_signal, signal_shift, star_null=False, star_real=False, mu=-2.5, null_graph="edges"):
    """Синтетический каталог: статистики пар независимы N(mu, 1); n_signal непересекающихся реальных пар (внутри половины разбиения 1)
    сдвинуты на signal_shift (истинные). star_null: все нулевые пары символа 0 сдвинуты на -4 (как DOSUSDT: нуль не обменяем с реальными).
    -> dict(real=(lo, hi, t, is_signal), nulls=[(lo, hi, t)] x 9)."""
    halves = []
    for k in range(3):
        perm = rng.permutation(nsym)
        h = np.zeros(nsym, dtype=int)
        h[perm[nsym // 2:]] = 1
        halves.append(h)
    pairs = [(i, j) for i in range(nsym) for j in range(i + 1, nsym)]
    lo = np.array([p[0] for p in pairs]); hi = np.array([p[1] for p in pairs])
    inside = np.zeros(len(pairs), dtype=bool)
    for h in halves:
        inside |= h[lo] == h[hi]
    t = mu + rng.randn(len(pairs))
    sig = np.zeros(len(pairs), dtype=bool)
    if n_signal:
        h0 = halves[0]
        cand = [i for i in range(len(pairs)) if h0[lo[i]] == h0[hi[i]]]
        used, picked = set(), []
        for i in rng.permutation(cand):
            if lo[i] in used or hi[i] in used:
                continue
            used.update((lo[i], hi[i])); picked.append(i)
            if len(picked) == n_signal:
                break
        sig[picked] = True
        t[picked] += signal_shift
    if star_real:
        t[(lo == 0) | (hi == 0)] += -4.0
    real = (lo[inside], hi[inside], t[inside], sig[inside])
    nulls = []
    if null_graph == "edges":              # r3.2 (D13): 9 повторов на ТЕХ ЖЕ рёбрах, что реальные пары
        l_, h_ = lo[inside], hi[inside]
        for _rep in range(9):
            tn = mu + rng.randn(len(l_))
            if star_null:
                tn = tn + np.where((l_ == 0) | (h_ == 0), -4.0, 0.0)
            nulls.append((l_, h_, tn))
        return dict(real=real, nulls=nulls)
    for k in range(3):                      # r3.1: двудольный граф A x B (≈ 0,57 рёбер реального графа) - оценка FDR оптимистична ≈ в 1,75 раза (M2-01)
        h = halves[k]
        cross = h[lo] != h[hi]
        for _shift in range(3):
            tn = mu + rng.randn(int(cross.sum()))
            l_, h_ = lo[cross], hi[cross]
            if star_null:
                tn = tn + np.where((l_ == 0) | (h_ == 0), -4.0, 0.0)
            nulls.append((l_, h_, tn))
    return dict(real=real, nulls=nulls)


def run_rep(cat, nsym, units):
    rlo, rhi, rt, rsig = cat["real"]
    if units == "bases":
        ra = F.accepted_stats(rlo, rhi, rt, nsym)
        na = np.sort(np.concatenate([F.accepted_stats(l, h, t, nsym) for l, h, t in cat["nulls"]]))
    else:      # единицы пар (прежний F7-вариант, для сравнения)
        ra = np.sort(rt)
        na = np.sort(np.concatenate([t for _, _, t in cat["nulls"]]))
    r = F.select_threshold(ra, na)
    if not r["ok"]:
        return 0, 0, 0
    if units == "bases":                     # реальный отбор = принятые паросочетанием пары <= t* (sel_fdrlib п. 5)
        m = np.zeros(len(rt), dtype=bool)
        m[F.matched_selection(rlo, rhi, rt, r["t_star"], nsym)] = True
    else:
        m = rt <= r["t_star"]
    return int(m.sum()), int((m & ~rsig).sum()), int((m & rsig).sum())


class SyntheticNull(unittest.TestCase):
    """«Доля ложных ≈ ожидаемой» на синтетике. Число повторов малое (быстро); допуски выбраны с запасом на шум оценки.
    Фактические числа печатаются в вывод unittest -v (используются в отчёте S1)."""
    NSYM = 60
    REPS = 120

    def _scan(self, units, **kw):
        sel = false = true_ = nonempty = 0
        fdp = []
        for rep in range(self.REPS):
            rng = np.random.RandomState(1000 + rep)
            cat = sim_catalog(rng, self.NSYM, **kw)
            s, f, tr = run_rep(cat, self.NSYM, units)
            sel += s; false += f; true_ += tr
            nonempty += int(s > 0)
            if s > 0:
                fdp.append(f / s)
        return dict(selected=sel, false=false, true=true_, nonempty=nonempty, fdp=float(np.mean(fdp)) if fdp else 0.0)

    def test_global_null(self):
        r = self._scan("bases", n_signal=0, signal_shift=0.0)
        print("\n  global null (bases): повторов %d, непустой отбор %d, отобрано пар %d" % (self.REPS, r["nonempty"], r["selected"]))
        self.assertLessEqual(r["nonempty"] / self.REPS, 0.15)        # при глобальном нуле FWER ≈ q = 0,10 или меньше

    def test_signal_fdp_below_q(self):
        r = self._scan("bases", n_signal=8, signal_shift=-4.0)
        fdr_emp = r["false"] / max(1, r["selected"])
        print("\n  signal (bases): отобрано %d, ложных %d (доля %.3f), истинных %d из %d, среднее FDP непустых %.3f"
              % (r["selected"], r["false"], fdr_emp, r["true"], 8 * self.REPS, r["fdp"]))
        # оценка оптимистична (выбор t* по максимуму кривой, малые k0): допуск - тройной заявленный уровень; число печатается для отчёта
        self.assertLessEqual(fdr_emp, 3 * 0.10)

    def test_star_in_null_bases_keep_power(self):
        # нуль необменяем с реальными: одна звезда в нуле. В единицах пар хвост нуля забит звездой (k0 ~ десятки), в единицах баз - не более 1 на набор
        rb = self._scan("bases", n_signal=8, signal_shift=-4.0, star_null=True)
        rp = self._scan("pairs", n_signal=8, signal_shift=-4.0, star_null=True)
        print("\n  звезда в нуле: истинных отобрано в единицах баз %d, в единицах пар %d (из %d); ложных %d / %d"
              % (rb["true"], rp["true"], 8 * self.REPS, rb["false"], rp["false"]))
        self.assertGreaterEqual(rb["true"], rp["true"])
        self.assertLessEqual(rb["false"] / max(1, rb["selected"]), 0.15)

    def test_star_in_real_bases_not_inflated(self):
        # звезда в реальных парах (символ 0 «коинтегрирован» со всеми): в единицах баз R учитывает её один раз
        rb = self._scan("bases", n_signal=0, signal_shift=0.0, star_real=True)
        print("\n  звезда в реальных: непустой отбор (bases) %d из %d, отобрано пар %d" % (rb["nonempty"], self.REPS, rb["selected"]))
        self.assertLessEqual(rb["nonempty"] / self.REPS, 0.25)

    def test_edges_vs_bipartite_null_bias(self):
        # M2-01 (аудит №2): нуль r3.1 (двудольный граф A x B, ≈ 0,57 рёбер реального) даёт оптимистичную оценку; нуль r3.2 (те же рёбра) возвращает долю ложных к номиналу
        r_old = self._scan("bases", n_signal=8, signal_shift=-4.0, null_graph="bipartite")
        r_new = self._scan("bases", n_signal=8, signal_shift=-4.0, null_graph="edges")
        f_old = r_old["false"] / max(1, r_old["selected"])
        f_new = r_new["false"] / max(1, r_new["selected"])
        print("\n  доля ложных среди отобранных (номинал 0,10): нуль r3.1 (двудольный) %.3f (отобрано %d), нуль r3.2 (те же рёбра) %.3f (отобрано %d)"
              % (f_old, r_old["selected"], f_new, r_new["selected"]))
        self.assertLessEqual(f_new, f_old + 0.02)                  # те же рёбра не хуже двудольного нуля
        self.assertLessEqual(f_new, 2 * 0.10)


# ---------------------------------------------------------------- сквозная синтетика на ЦЕНАХ (D13)
SPD = 48                                     # отсчётов в сутки (30 мин); 14 суток формирования, окно EG - 11 суток со смещением источника {-2, -1, 0, +1} суток
NDAYS, WIN_DAYS = 14, 11
OFF_DAYS = {"p2": -2, "p1": -1, "r": 0, "m1": 1}


def window(X, off):
    a = (2 + OFF_DAYS[off]) * SPD            # реальное окно - с 3-х суток формирования (03.10), как в sel_shift
    return X[:, a:a + WIN_DAYS * SPD]


def eg_mat(Y, X):
    """T[i, j] = max(t_первая_половина, t_вторая) DF-статистики (лаг 0, без константы, как eg.rs) остатков регрессии Y_i на X_j (с константой) на каждой половине."""
    n, L = Y.shape
    mid = L // 2
    halves = []
    for a, b in ((0, mid), (mid, L)):
        Yc = Y[:, a:b] - Y[:, a:b].mean(1, keepdims=True)
        Xc = X[:, a:b] - X[:, a:b].mean(1, keepdims=True)
        T = np.empty((n, X.shape[0]))
        for j in range(X.shape[0]):
            xc = Xc[j]
            beta = (Yc @ xc) / (xc @ xc)
            U = Yc - beta[:, None] * xc[None, :]
            u1 = U[:, :-1]
            d = U[:, 1:] - u1
            den = (u1 * u1).sum(1)
            g = (d * u1).sum(1) / den
            res = d - g[:, None] * u1
            s2 = (res * res).sum(1) / (d.shape[1] - 1)
            T[:, j] = g / np.sqrt(s2 / den)
        halves.append(T)
    return np.maximum(halves[0], halves[1])


def sym_stat(A, B=None):
    """Статистика неупорядоченной пары (i из A, j из B): лучшее из двух направлений; B = None - обе серии из A (реальные пары)."""
    if B is None:
        T = eg_mat(A, A)
        return np.minimum(T, T.T)
    return np.minimum(eg_mat(A, B), eg_mat(B, A).T)


def sim_prices(rng, nsym, n_pairs, factor_sd, phi=0.6, noise=0.3):
    """Логарифмы цен: независимые блуждания + общий рыночный фактор (factor_sd) + n_pairs коинтегрированных пар (j = 0,8 i + OU)."""
    T = NDAYS * SPD
    f = np.cumsum(rng.randn(T))
    beta = 1.0 + 0.3 * rng.randn(nsym)
    X = np.cumsum(rng.randn(nsym, T), axis=1) + factor_sd * beta[:, None] * f[None, :]
    perm = rng.permutation(nsym)
    sig = set()
    for k in range(n_pairs):
        i, j = int(perm[2 * k]), int(perm[2 * k + 1])
        u = np.zeros(T)
        e = noise * rng.randn(T)
        for t in range(1, T):
            u[t] = phi * u[t - 1] + e[t]
        X[j] = 0.8 * X[i] + u
        sig.add((min(i, j), max(i, j)))
    return X, sig


def run_world(rng, nsym, n_pairs, factor_sd):
    """Один мир: цены -> статистики реальных пар (внутри половины хотя бы в одном из 3 разбиений) и 12 выравниваний на ребро -> sel_fdrlib.
    -> (отобрано, ложных, истинных, исход, число истинных пар среди рёбер графа)."""
    X, sig = sim_prices(rng, nsym, n_pairs, factor_sd)
    real_T = sym_stat(window(X, "r"))
    TT = np.full((len(SH.RUNS), nsym, nsym), np.nan)
    for run, (o1, o2) in enumerate(SH.RUNS):
        TT[run] = sym_stat(window(X, o1), window(X, o2))
        np.fill_diagonal(TT[run], np.nan)                    # один символ при двух смещениях - не пара
    H = [np.zeros(nsym, dtype=int) for _ in range(3)]
    for h in H:
        h[rng.permutation(nsym)[nsym // 2:]] = 1
    iu = np.triu_indices(nsym, 1)
    lo, hi = iu[0], iu[1]
    inside = np.zeros(len(lo), dtype=bool)
    for h in H:
        inside |= h[lo] == h[hi]
    lo, hi = lo[inside], hi[inside]
    rt = real_T[lo, hi]
    perm = F.draw_alignments(len(lo))
    reps = F.repeat_matchings(TT, lo, hi, perm, list(range(SH.N_TRAIN)), nsym)
    null_acc = np.sort(np.concatenate([t[idx] for _, _, t, idx in reps]))
    real_idx = F.greedy_matching(lo, hi, rt, nsym)
    res = F.select_threshold(np.sort(rt[real_idx]), null_acc)
    pot = sum(1 for a, b in zip(lo.tolist(), hi.tolist()) if (a, b) in sig)
    if not res["ok"]:
        return 0, 0, 0, res["kind"], pot
    sel = F.matched_selection(lo, hi, rt, res["t_star"], nsym)
    pairs = [(int(lo[i]), int(hi[i])) for i in sel]
    tr = sum(1 for p_ in pairs if p_ in sig)
    return len(pairs), len(pairs) - tr, tr, res["kind"], pot


class EndToEnd(unittest.TestCase):
    """Нуль-мир и мир с сигналом на ЦЕНАХ (не на готовых статистиках): те же смещения серий, те же рёбра, тот же отбор, что sel_fdr.py. Цифры печатаются (отчёт S2)."""
    NSYM = 60
    REPS = 40

    def _scan(self, n_pairs, factor_sd, seed0):
        sel = false = true_ = nonempty = pot = 0
        fdp = []
        kinds = {}
        for rep in range(self.REPS):
            rng = np.random.RandomState(seed0 + rep)
            s, f, t, kind, npot = run_world(rng, self.NSYM, n_pairs, factor_sd)
            sel += s; false += f; true_ += t; pot += npot
            nonempty += int(s > 0)
            kinds[kind] = kinds.get(kind, 0) + 1
            if s > 0:
                fdp.append(f / s)
        return dict(selected=sel, false=false, true=true_, nonempty=nonempty, fdp=float(np.mean(fdp)) if fdp else 0.0, kinds=kinds, potential=pot)

    def test_null_world_independent_walks(self):
        r = self._scan(0, 0.0, 7000)
        print("\n  нуль-мир (независимые блуждания): миров %d, непустой отбор %d, отобрано пар %d, исходы %s" % (self.REPS, r["nonempty"], r["selected"], r["kinds"]))
        self.assertLessEqual(r["nonempty"] / self.REPS, 0.25)

    def test_null_world_common_factor(self):
        r = self._scan(0, 1.0, 7500)
        print("\n  нуль-мир с общим рыночным фактором: миров %d, непустой отбор %d, отобрано пар %d, исходы %s" % (self.REPS, r["nonempty"], r["selected"], r["kinds"]))
        self.assertLessEqual(r["nonempty"] / self.REPS, 0.45)       # фактор делает реальные пары экстремальнее сдвинутого нуля (L5-03): оговорка отчёта

    def test_signal_world(self):
        r = self._scan(12, 0.0, 8000)
        fd = r["false"] / max(1, r["selected"])
        print("\n  мир с 12 истинными парами: миров %d, отобрано %d, ложных %d (доля %.3f; номинал 0,10), истинных %d из %d в графе, среднее FDP непустых %.3f, исходы %s"
              % (self.REPS, r["selected"], r["false"], fd, r["true"], r["potential"], r["fdp"], r["kinds"]))
        self.assertGreater(r["true"], 0)
        self.assertLessEqual(fd, 3 * 0.10)

    def test_signal_world_common_factor(self):
        r = self._scan(12, 1.0, 8500)
        fd = r["false"] / max(1, r["selected"])
        print("\n  мир с 12 истинными парами и общим фактором: миров %d, отобрано %d, ложных %d (доля %.3f), истинных %d из %d в графе, исходы %s"
              % (self.REPS, r["selected"], r["false"], fd, r["true"], r["potential"], r["kinds"]))
        self.assertLessEqual(fd, 0.5)


if __name__ == "__main__":
    unittest.main()
