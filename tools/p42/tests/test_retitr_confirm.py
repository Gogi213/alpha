"""Тесты охраны `retitr_report.py confirm` (срез `--days` — номера строк daily от P42_T0, не сутки сетки; ошибка чтения августа 18.11).
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_retitr_confirm.py` — выполняет все test_* и печатает итог.
"""
import csv
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import retitr_report as rr  # noqa: E402

AUG = "2026-08-01:2026-08-31"
T0_AUG = "2026-07-18"


def mk_cand(d, rows=60, last_trade_day=30, tamper=False):
    """Синтетический прогон: строки daily 0…rows-1, по одной сделке в сутки 0…last_trade_day (net = 1 + сутки), данные после last_trade_day пустые — как в августе."""
    with open(os.path.join(d, "long-trades.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "level", "sig_min", "entry_min", "exit_min", "entry_px_e8", "kind", "takes", "gross", "fees", "funding", "net", "entry_day", "sig_ms", "entry_ms", "exit_ms"])
        for k in range(last_trade_day + 1):
            w.writerow(["AAAUSDT" if k % 2 else "BBBUSDT", 1, 0, 0, 0, 1, "trail", 1, 0, 0, 0, "%.6f" % (1.0 + k), k, 0, k * 86_400_000 + 1, 0])
    with open(os.path.join(d, "long-daily.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("day,pnl\n")
        for k in range(rows):
            v = (1.0 + k) if k <= last_trade_day else 0.0
            if tamper and k == 5:
                v += 3.0
            f.write("%d,%.6f\n" % (k, v))
    with open(os.path.join(d, "long-coinday.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("sym,day,pnl\n")
        for k in range(last_trade_day + 1):
            f.write("%s,%d,%.6f\n" % ("AAAUSDT" if k % 2 else "BBBUSDT", k, 1.0 + k))


def raises_exit(fn):
    try:
        fn()
    except SystemExit as e:
        return str(e)
    return None


def test_window_dates():
    assert rr.window_dates(T0_AUG, 0, 31) == (datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    assert rr.window_dates("2026-09-01", 0, 60) == (datetime.date(2026, 9, 15), datetime.date(2026, 11, 13))
    assert rr.window_dates("2026-10-31", 0, 3) == (datetime.date(2026, 11, 14), datetime.date(2026, 11, 16))


def test_absolute_days_rejected():
    """`14:45` (сутки сетки вместо номеров строк) — это 15.08…14.09, не август: остановка."""
    assert rr.check_window(T0_AUG, AUG, 0, 31) == (datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
    msg = raises_exit(lambda: rr.check_window(T0_AUG, AUG, 14, 45))
    assert msg and "2026-08-15" in msg and "2026-09-14" in msg, msg
    assert raises_exit(lambda: rr.check_window(T0_AUG, AUG, 0, 30)) is not None
    assert raises_exit(lambda: rr.check_window("2026-09-01", AUG, 0, 31)) is not None   # не тот P42_T0


def test_confirm_lines_ok_and_wrong_slice():
    with tempfile.TemporaryDirectory() as d:
        mk_cand(d)
        lines, v = rr.confirm_lines("long", d, 0, 31, T0_AUG, AUG)
        assert "2026-08-01…2026-08-31" in lines[0] and "31 сут." in lines[0], lines[0]
        assert "сделок 31, сумма +496.00" in lines[1], lines[1]       # 1 + 2 + … + 31
        # тот же прогон, срез 14:45 при заявленном августе — остановка до чтения (охрана срабатывает на календарном окне)
        assert raises_exit(lambda: rr.confirm_lines("long", d, 14, 45, T0_AUG, AUG)) is not None


def test_sum_guard():
    """Охрана суммы: сумма строк среза daily обязана равняться сумме net сделок с entry_day в окне."""
    with tempfile.TemporaryDirectory() as d:
        mk_cand(d, tamper=True)
        msg = raises_exit(lambda: rr.confirm_lines("long", d, 0, 31, T0_AUG, AUG))
        assert msg and "не сходится" in msg, msg
    with tempfile.TemporaryDirectory() as d:
        mk_cand(d, rows=20)                                             # daily короче среза
        msg = raises_exit(lambda: rr.confirm_lines("long", d, 0, 31, T0_AUG, AUG))
        assert msg and "выходит за них" in msg, msg


def mk_cand_series(d, vals, side="long", syms=("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT")):
    """Синтетический прогон по заданному суточному ряду: по одной сделке в сутки (net = vals[k], 6 монет по кругу — устойчивость «без 2 лучших монет» не обнуляет ряд); coinday — те же числа."""
    with open(os.path.join(d, side + "-trades.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "level", "sig_min", "entry_min", "exit_min", "entry_px_e8", "kind", "takes", "gross", "fees", "funding", "net", "entry_day", "sig_ms", "entry_ms", "exit_ms"])
        for k, v in enumerate(vals):
            w.writerow([syms[k % len(syms)], 1, 0, 0, 0, 1, "trail", 1, 0, 0, 0, "%.6f" % v, k, 0, k * 86_400_000 + 1, 0])
    with open(os.path.join(d, side + "-daily.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("day,pnl\n")
        for k, v in enumerate(vals):
            f.write("%d,%.6f\n" % (k, v))
    with open(os.path.join(d, side + "-coinday.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("sym,day,pnl\n")
        for k, v in enumerate(vals):
            f.write("%s,%d,%.6f\n" % (syms[k % len(syms)], k, v))


def test_holm_reject():
    """Холм step-down: порог alpha/(m − k + 1); первый провал останавливает; порядок входа сохраняется."""
    assert rr.holm_reject([0.001, 0.02, 0.5]) == [True, True, False]           # 0.001 ≤ 0.0167; 0.02 ≤ 0.025; 0.5 > 0.05
    assert rr.holm_reject([0.02, 0.001, 0.5]) == [True, True, False]           # порядок входа сохраняется
    assert rr.holm_reject([0.001, 0.03, 0.04]) == [True, False, False]         # 0.03 > 0.025 — стоп, 0.04 не проверяется
    assert rr.holm_reject([0.02, 0.03, 0.04]) == [False, False, False]         # наименьший 0.02 > 0.05/3 — стоп сразу
    assert rr.holm_reject([0.01, 0.02, 0.03]) == [True, True, True]            # 0.01 ≤ 0.0167; 0.02 ≤ 0.025; 0.03 ≤ 0.05
    assert rr.holm_reject([0.04]) == [True] and rr.holm_reject([0.06]) == [False]


def test_onesided_p_matches_lower():
    """p < 0,05 ⇔ нижняя 5 %-граница > 0 (одна и та же доля бутстреп-средних)."""
    import numpy as np
    rng = np.random.default_rng(1)
    for shift in (-1.0, 0.2, 0.8, 3.0):
        st = rr.onesided(rng.normal(shift, 3.0, 31))
        assert (st["p"] < 0.05) == (st["lower"] > 0) or abs(st["p"] - 0.05) < 1e-3, (shift, st)


def test_family_window_guard_and_verdicts():
    """confirm-family: срез/окно проверяются как в confirm (неверное окно — остановка); сильный положительный ряд — «подтверждено», слабый — «не ясно», отрицательный — «опровергнуто»."""
    import numpy as np
    rng = np.random.default_rng(7)
    strong = list(10.0 + rng.normal(0, 1.0, 31))
    weak = list(rng.normal(0.3, 6.0, 31))
    neg = list(-8.0 + rng.normal(0, 1.0, 31))
    wins = {"aug": (T0_AUG, AUG, 0, 31)}
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, tempfile.TemporaryDirectory() as d3:
        mk_cand_series(d1, strong, "long"); mk_cand_series(d2, weak, "short"); mk_cand_series(d3, neg, "short")
        cfgs = [("S", "long", [("aug", d1)]), ("W", "short", [("aug", d2)]), ("N", "short", [("aug", d3)])]
        lines, data = rr.family_lines(cfgs, wins, "repl-aug")
        assert data["m"] == 3 and data["configs"]["S"]["verdict"] == "подтверждено", data["configs"]["S"]["verdict"]
        assert data["configs"]["W"]["verdict"] == "не ясно", data["configs"]["W"]["verdict"]
        assert data["configs"]["N"]["verdict"] == "опровергнуто", data["configs"]["N"]["verdict"]
        assert "m = 3, Холм" in lines[0], lines[0]
        assert raises_exit(lambda: rr.family_lines(cfgs, {"aug": (T0_AUG, AUG, 14, 45)}, "x")) is not None      # срез 14:45 при заявленном августе
        assert raises_exit(lambda: rr.family_lines(cfgs, {"aug": ("2026-06-17", AUG, 0, 31)}, "x")) is not None  # не тот P42_T0


def test_family_pooled_windows_and_sign():
    """Клетка с двумя окнами — один ряд 62 сут.; «подтверждено» требует среднее > 0 и в каждом окне отдельно: сильный июль + умеренно отрицательный август дают объединённый плюс, но не «подтверждено»."""
    import numpy as np
    rng = np.random.default_rng(11)
    jul_ok, aug_ok = list(10.0 + rng.normal(0, 1.0, 31)), list(9.0 + rng.normal(0, 1.0, 31))
    aug_bad = list(-1.0 + rng.normal(0, 1.0, 31))
    wins = {"jul": ("2026-06-17", "2026-07-01:2026-07-31", 0, 31), "aug": (T0_AUG, AUG, 0, 31)}
    with tempfile.TemporaryDirectory() as dj, tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db, tempfile.TemporaryDirectory() as dx:
        mk_cand_series(dj, jul_ok, "short"); mk_cand_series(da, aug_ok, "short"); mk_cand_series(db, aug_bad, "short"); mk_cand_series(dx, jul_ok, "long")
        lines, data = rr.family_lines([("P", "short", [("jul", dj), ("aug", da)]), ("Q", "short", [("jul", dj), ("aug", db)]), ("J", "long", [("jul", dx)])], wins, "confirm")
        assert data["configs"]["P"]["verdict"] == "подтверждено" and data["configs"]["P"]["trades"]["n"] == 62, data["configs"]["P"]
        assert data["configs"]["Q"]["window_means"][1] < 0 and data["configs"]["Q"]["verdict"] != "подтверждено", data["configs"]["Q"]
        assert any("62 сут." in l for l in lines), lines[:4]
        assert any("окно jul" in l for l in lines) and any("окно aug" in l for l in lines)


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for n in names:
        globals()[n]()
        print("ok", n)
    print("все %d тестов прошли" % len(names))
