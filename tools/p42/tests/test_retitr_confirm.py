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


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for n in names:
        globals()[n]()
        print("ok", n)
    print("все %d тестов прошли" % len(names))
