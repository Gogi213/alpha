"""TK-493 w3: разностный ряд фандинга D2 (statarb/xdl9/d2_diff_funding.py) на синтетике.

Проверяется: привязка выплат X к меткам Y без двойного счёта и без заглядывания вперёд, сохранение суммы, остаток
(голова/хвост), равные и неравные сетки, шарды при общем Y, формат файла (читается так же, как ядро), равенство
начисления «разность на ноге Y, у X пусто» и «обе ноги по отдельности» на равных сетках (правило ядра: выплата
начисляется за минуты строго между исполнением входа и исполнением выхода).
"""
import csv, gzip, importlib.util, os, random, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("d2_diff_funding", os.path.join(HERE, "..", "statarb", "xdl9", "d2_diff_funding.py"))
d2 = importlib.util.module_from_spec(spec)
sys.modules["d2_diff_funding"] = d2
spec.loader.exec_module(d2)

G0 = d2.GRID0_MS
HOUR = 3_600_000


def mk(times_h, rates, itv, base_ms=G0 + 2465 * 1440 * 60_000, shift_ms=0):
    """Строки (time_ms, rate, interval_txt, time_txt) как после read_funding."""
    out = []
    for h, r in zip(times_h, rates):
        t = base_ms + int(h * HOUR) + shift_ms
        out.append((t, r, str(itv), str(t)))
    return out


def write_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("time_ms,rate,interval_h\n")
        for t, r, itv in rows:
            fh.write("%s,%s,%s\n" % (t, repr(r), itv))


def kernel_parse(path):
    """Как cli::parse_funding: пропустить заголовок, взять два первых поля -> (funding_min, rate)."""
    v = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh.read().splitlines()[1:]:
            f = line.split(",")
            v.append((d2.funding_min(int(f[0])), float(f[1])))
    return v


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def read_manifest(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def accrual(pos_dir, entry_min, exit_min, events):
    """Правило ядра для постоянных номиналов (цены равны, β = 1): выплата за минуты строго между входом и выходом;
    лонг платит ставку, шорт получает: начисление = −dir·rate."""
    return -pos_dir * sum(r for m, r in events if entry_min < m < exit_min)


class EqualGrid(unittest.TestCase):
    def test_equal_8h_grid(self):
        ry = mk([0, 8, 16, 24], [1e-4, 2e-4, -1e-4, 3e-4], 8)
        rx = mk([0, 8, 16, 24], [5e-5, 1e-4, 2e-4, 1e-4], 8)
        rows, st = d2.diff_series(ry, rx)
        self.assertEqual([r[1] for r in rows], [1e-4 - 5e-5, 2e-4 - 1e-4, -1e-4 - 2e-4, 3e-4 - 1e-4])
        self.assertEqual(st["grid_equal"], 1)
        self.assertEqual((st["x_head"], st["x_tail"], st["x_attached"]), (0, 0, 4))
        self.assertLess(st["mass_err"], d2.MASS_TOL)
        # время и интервал ноги Y сохранены побайтно
        self.assertEqual([r[0] for r in rows], [y[3] for y in ry])
        self.assertEqual([r[2] for r in rows], ["8"] * 4)

    def test_seconds_offset_inside_minute_and_before_mark(self):
        # X на 1 с позже и на 1 с раньше метки Y: обе привязываются к своей метке, не к соседней
        ry = mk([0, 8], [1e-4, 1e-4], 8)
        rx_late = mk([0, 8], [3e-5, 4e-5], 8, shift_ms=1000)
        rows, st = d2.diff_series(ry, rx_late)
        self.assertEqual(st["grid_equal"], 1)
        self.assertAlmostEqual(rows[0][1], 1e-4 - 3e-5, places=18)
        rx_early = mk([0, 8], [3e-5, 4e-5], 8, shift_ms=-1000)   # 07:59:59 и 23:59:59 предыдущих суток
        rows2, st2 = d2.diff_series(ry, rx_early)
        # первая X (за 1 с до первой метки) привязана к метке 0 (первый интервал по interval_h = 8 ч), вторая — к метке 1
        self.assertEqual(st2["x_head"], 0)
        self.assertAlmostEqual(rows2[0][1], 1e-4 - 3e-5, places=18)
        self.assertAlmostEqual(rows2[1][1], 1e-4 - 4e-5, places=18)

    def test_no_double_count_equals_two_leg_accrual(self):
        rnd = random.Random(37)
        hours = list(range(0, 8 * 30, 8))
        ry = mk(hours, [rnd.uniform(-5e-4, 5e-4) for _ in hours], 8)
        rx = mk(hours, [rnd.uniform(-5e-4, 5e-4) for _ in hours], 8)
        rows, st = d2.diff_series(ry, rx)
        self.assertEqual(st["grid_equal"], 1)
        ev_y = [(d2.funding_min(t), r) for t, r, _, _ in ry]
        ev_x = [(d2.funding_min(t), r) for t, r, _, _ in rx]
        ev_d = [(d2.funding_min(int(r[0])), r[1]) for r in rows]
        last = d2.funding_min(ry[-1][0]) + 600
        first = d2.funding_min(ry[0][0]) - 600
        for _ in range(400):
            a, b = sorted(rnd.sample(range(first, last), 2))
            dr = rnd.choice((-1, 1))
            # длинная нога Y платит r_Y, короткая X получает r_X; чистое начисление dir=+1: −(r_Y − r_X)
            two_legs = accrual(dr, a, b, ev_y) + accrual(-dr, a, b, ev_x)
            diff_only = accrual(dr, a, b, ev_d)          # у X пусто (--funding-x: каталог без файлов)
            self.assertAlmostEqual(two_legs, diff_only, places=15)


class UnequalGrid(unittest.TestCase):
    def test_y_8h_x_hourly_sums_and_tail(self):
        ry = mk([0, 8, 16], [1e-4, 2e-4, 3e-4], 8)
        xh = list(range(-3, 24))            # -3..23 ч: часы -3,-2,-1 до первой метки, 17..23 — после последней метки (16 ч)
        rates = [1e-5 * (i + 1) for i in range(len(xh))]
        rx = mk(xh, rates, 1)
        rows, st = d2.diff_series(ry, rx)
        # интервал первой метки: 8 ч => (−8; 0], выплаты −3..0 привязаны к метке 0 (час 0 включительно)
        want0 = 1e-4 - sum(r for h, r in zip(xh, rates) if -8 < h <= 0)
        want1 = 2e-4 - sum(r for h, r in zip(xh, rates) if 0 < h <= 8)
        want2 = 3e-4 - sum(r for h, r in zip(xh, rates) if 8 < h <= 16)
        self.assertAlmostEqual(rows[0][1], want0, places=15)
        self.assertAlmostEqual(rows[1][1], want1, places=15)
        self.assertAlmostEqual(rows[2][1], want2, places=15)
        self.assertEqual(st["grid_equal"], 0)
        self.assertEqual(st["x_tail"], 7)          # часы 17..23
        self.assertEqual(st["x_head"], 0)
        self.assertEqual(st["x_attached"] + st["x_head"] + st["x_tail"], len(rx))
        self.assertLess(st["mass_err"], d2.MASS_TOL)

    def test_head_dropped_beyond_first_interval(self):
        ry = mk([8, 16], [1e-4, 1e-4], 8)
        rx = mk([-5, 0, 7, 8], [1e-5, 2e-5, 3e-5, 4e-5], 8)   # (0; 8] — интервал первой метки; −5 и 0 вне интервала
        rows, st = d2.diff_series(ry, rx)
        self.assertEqual(st["x_head"], 2)
        self.assertAlmostEqual(rows[0][1], 1e-4 - (3e-5 + 4e-5), places=18)
        self.assertLess(st["mass_err"], d2.MASS_TOL)

    def test_y_hourly_x_8h_one_of_eight(self):
        ry = mk(list(range(0, 17)), [1e-5] * 17, 1)
        rx = mk([0, 8, 16], [8e-5, 8e-5, 8e-5], 8)
        rows, st = d2.diff_series(ry, rx)
        # выплата X на минуте метки Y привязана к этой метке; в остальные часы разность равна ставке Y
        d = [r[1] for r in rows]
        self.assertAlmostEqual(d[0], 1e-5 - 8e-5, places=18)
        self.assertAlmostEqual(d[8], 1e-5 - 8e-5, places=18)
        self.assertAlmostEqual(d[16], 1e-5 - 8e-5, places=18)
        self.assertEqual(sum(1 for v in d if abs(v - 1e-5) < 1e-18), 14)
        self.assertEqual(st["grid_equal"], 0)

    def test_causal_never_later_than_mark(self):
        # каждая выплата X привязана к метке Y с минутой >= минуты выплаты: ряд не видит будущего
        rnd = random.Random(5)
        my_h = sorted(rnd.sample(range(0, 400), 40))
        ry = mk(my_h, [1e-4] * len(my_h), 4)
        xh = sorted(rnd.sample(range(0, 400), 90))
        rx = mk(xh, [rnd.uniform(-1e-4, 1e-4) for _ in xh], 1)
        rows, st = d2.diff_series(ry, rx)
        my = [d2.funding_min(t) for t, _, _, _ in ry]
        # восстановим привязку: d_k = r_Y − Σ r_X(привязанных); проверим по времени: сумма X на (m_{k-1}, m_k]
        for k in range(1, len(ry)):
            s = sum(r for t, r, _, _ in rx if my[k - 1] < d2.funding_min(t) <= my[k])
            self.assertAlmostEqual(rows[k][1], ry[k][1] - s, places=15)
        self.assertLess(st["mass_err"], d2.MASS_TOL)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        for sub in ("fy", "fx", "out"):
            os.makedirs(os.path.join(self.t, sub))

    def tearDown(self):
        self.tmp.cleanup()

    def _put(self, sub, name, times_h, rates, itv):
        write_gz(os.path.join(self.t, sub, name + ".csv.gz"), [(r[0], r[1], r[2]) for r in mk(times_h, rates, itv)])

    def _pairs(self, pairs):
        p = os.path.join(self.t, "pairs.csv")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("ex_y,y,ex_x,x\n")
            for y, x in pairs:
                fh.write("binance,%s,bybit,%s\n" % (y, x))
        return p

    def test_end_to_end_shards_manifest_and_format(self):
        self._put("fy", "BTCUSDT", [0, 8, 16], [1e-4, 2e-4, 3e-4], 8)
        self._put("fy", "MINIMAXUSDT", [0, 8], [1e-4, 1e-4], 8)
        self._put("fx", "BTCUSDT", [0, 8, 16], [5e-5, 5e-5, 5e-5], 8)
        self._put("fx", "MINIMAXUSDT", [0, 8], [2e-5, 2e-5], 8)
        self._put("fx", "MINIMAXHKDUSDT", [0, 8], [3e-5, 3e-5], 8)
        pairs = self._pairs([("BTCUSDT", "BTCUSDT"), ("MINIMAXUSDT", "MINIMAXUSDT"), ("MINIMAXUSDT", "MINIMAXHKDUSDT"),
                             ("NOPE", "BTCUSDT")])
        out = os.path.join(self.t, "out")
        rc = d2.main(["--pairs", pairs, "--fund-y", os.path.join(self.t, "fy"), "--fund-x", os.path.join(self.t, "fx"),
                      "--out", out, "--from-day", "2479", "--to-day", "2500"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isdir(os.path.join(out, "empty_x")))
        self.assertEqual(os.listdir(os.path.join(out, "empty_x")), [])
        man = read_manifest(os.path.join(out, "manifest.csv"))
        self.assertEqual([(m["y"], m["x"], m["shard"], m["status"]) for m in man],
                         [("BTCUSDT", "BTCUSDT", "0", "ok"), ("MINIMAXUSDT", "MINIMAXUSDT", "0", "ok"),
                          ("MINIMAXUSDT", "MINIMAXHKDUSDT", "1", "ok"), ("NOPE", "BTCUSDT", "0", "нет файла ставок: Y")])
        # файл читается так же, как ядро (заголовок, два первых поля), время и интервал Y не тронуты
        v0 = kernel_parse(os.path.join(out, "shard0", "BTCUSDT.csv.gz"))
        self.assertEqual([r for _, r in v0], [1e-4 - 5e-5, 2e-4 - 5e-5, 3e-4 - 5e-5])
        v1 = kernel_parse(os.path.join(out, "shard1", "MINIMAXUSDT.csv.gz"))
        self.assertEqual([r for _, r in v1], [1e-4 - 3e-5, 1e-4 - 3e-5])
        v00 = kernel_parse(os.path.join(out, "shard0", "MINIMAXUSDT.csv.gz"))
        self.assertEqual([r for _, r in v00], [1e-4 - 2e-5, 1e-4 - 2e-5])
        with gzip.open(os.path.join(out, "shard0", "BTCUSDT.csv.gz"), "rt") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "time_ms,rate,interval_h")
        self.assertTrue(all(l.endswith(",8") for l in lines[1:]))
        # списки заданий шардов: 4 поля, как read_jobs
        j0 = read_lines(os.path.join(out, "shard0.jobs.csv"))
        self.assertEqual(j0, ["y,x,from_day,to_day", "BTCUSDT,BTCUSDT,2479,2500", "MINIMAXUSDT,MINIMAXUSDT,2479,2500"])
        j1 = read_lines(os.path.join(out, "shard1.jobs.csv"))
        self.assertEqual(j1, ["y,x,from_day,to_day", "MINIMAXUSDT,MINIMAXHKDUSDT,2479,2500"])

    def test_sum_of_diff_equals_sum_y_minus_attached_x(self):
        rnd = random.Random(11)
        hy = list(range(0, 24 * 10, 4))
        hx = list(range(0, 24 * 10, 1))
        self._put("fy", "A", hy, [rnd.uniform(-1e-3, 1e-3) for _ in hy], 4)
        self._put("fx", "A", hx, [rnd.uniform(-1e-3, 1e-3) for _ in hx], 1)
        pairs = self._pairs([("A", "A")])
        out = os.path.join(self.t, "out")
        rc = d2.main(["--pairs", pairs, "--fund-y", os.path.join(self.t, "fy"), "--fund-x", os.path.join(self.t, "fx"), "--out", out])
        self.assertEqual(rc, 0)
        m = read_manifest(os.path.join(out, "manifest.csv"))[0]
        self.assertLess(float(m["mass_err"]), d2.MASS_TOL)
        self.assertAlmostEqual(float(m["sum_d"]), float(m["sum_y"]) - float(m["sum_x_attached"]), places=14)
        self.assertEqual(int(m["x_attached"]) + int(m["x_head"]) + int(m["x_tail"]), len(hx))
        self.assertEqual(m["grid_equal"], "0")
        self.assertFalse(os.path.exists(os.path.join(out, "shard0.jobs.csv")))   # дни не заданы — список заданий не пишется


if __name__ == "__main__":
    unittest.main()
