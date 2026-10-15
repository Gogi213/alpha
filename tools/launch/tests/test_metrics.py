import os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import metrics

A, D, H = metrics.month_bounds("2026-02")[0], metrics.MS_DAY, 3_600_000   # 2026-02-01 UTC, 28 суток


def test_month_bounds():
    assert metrics.month_bounds("2026-02") == (1769904000000, 1772323200000)
    assert metrics.month_bounds("2026-12")[1] - metrics.month_bounds("2026-12")[0] == 31 * D


def test_hash_metrics_by_hand():
    tr = [[A + 3 * D + H, 6.0, "ETH"], [A + H, 10.0, "BTC"], [A + D + H, -4.0, "BTC"], [A + 10 * D + H, -2.0, "SOL"]]   # порядок не по времени
    m = metrics.hash_metrics({"2026-02": tr})
    day = [0.0] * 28
    day[0], day[1], day[3], day[10] = 10.0, -4.0, 6.0, -2.0
    assert (m["n"], m["pnl"], m["winrate"], m["months"]) == (4, 10.0, 0.5, 1)
    assert abs(m["sharpe"] - statistics.mean(day) / statistics.stdev(day)) < 1e-4
    assert m["overhigh_d"] == 3.0                      # максимум 10 (сутки 0) -> новый максимум 12 (сутки 3): ровно 3 суток
    assert m["tail_d"] == round(25 - 1 / 24, 2)        # от последнего максимума (сутки 3 + 1 ч) до конца месяца
    assert m["worst_d"] == m["tail_d"]
    assert m["by_month"]["2026-02"]["pnl"] == 10.0


def test_no_trades_is_none_not_zero():
    m = metrics.hash_metrics({"2026-02": []})
    assert m["n"] == 0 and m["pnl"] is None and m["sharpe"] is None and m["winrate"] is None and m["worst_d"] is None
    assert m["months"] == 1 and m["by_month"]["2026-02"]["n"] == 0


def test_spill_after_month_end_is_kept():
    m = metrics.hash_metrics({"2026-02": [[A + H, 5.0, "BTC"], [A + 28 * D + 30 * 60_000, 2.0, "BTC"]]})   # закрытие 01.03 00:30 (В-94)
    assert m["n"] == 2 and m["pnl"] == 7.0             # сумма как в Летописи (все закрытия ключа месяца)
    assert m["overhigh_d"] == round(28 / 1 + 30 / 1440 - 1 / 24, 2)   # второй максимум — на 28-е сутки после первого


def test_flat_and_zero_accounts():
    m = metrics.hash_metrics({"2026-02": [[A + H, 1.0, "BTC"], [A + D + H, -1.0, "BTC"]]})
    assert m["sharpe"] is not None and m["winrate"] == 0.5
    same_day = metrics.hash_metrics({"2026-02": [[A + H, 1.0, "BTC"], [A + 2 * H, -1.0, "BTC"]]})   # сутки в сумме 0 -> σ = 0 -> Шарпа нет
    assert same_day["sharpe"] is None and same_day["n"] == 2
    m0 = metrics.hash_metrics({"2026-02": [[A + H, 0.0, "BTC"], [A + D + H, 0.0, "BTC"]]})
    assert m0["sharpe"] is None and m0["winrate"] == 0.0 and m0["overhigh_d"] == 0 and m0["tail_d"] == 28.0   # максимум так и остался стартовым 0


def test_best_by_sharpe_skips_missing():
    pm = {"a": {"sharpe": 0.1}, "b": {"sharpe": 0.3}, "c": {"sharpe": None}, "d": None}
    assert metrics.best_by_sharpe(pm)[0] == "b"
    assert metrics.best_by_sharpe({"c": {"sharpe": None}}) == (None, None)
