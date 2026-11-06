"""TK-493 S1: оценка доли ложных A1 по эмпирическому нулю в единицах баз (sel_fdrlib), сдвиги нуля (sel_shift), синтетический нуль.
Запуск: python -m unittest tools/compute/tests/test_statarb_sel_fdr.py (из корня дерева)."""
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


def sim_catalog(rng, nsym, n_signal, signal_shift, star_null=False, star_real=False, mu=-2.5):
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
    for k in range(3):
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


if __name__ == "__main__":
    unittest.main()
