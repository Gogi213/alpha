#!/usr/bin/env python3
"""`_lib.metrics` (T-21): день/месяц по сигналу, Шарп с нулевыми сутками, и парная проверка —
`drawdown_closed` даёт то же число, что `portfolio-sim.closed_drawdown` на одной и той же серии
(портирование формулы без изменений, canon §2/§5 «откат по закрытиям», П2).

Запуск: `python -m pytest tools/compute/tests -q`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def load(qualname: str, filename: str):
    spec = importlib.util.spec_from_file_location(qualname, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = mod
    spec.loader.exec_module(mod)
    return mod


metrics = load("_lib.metrics", "_lib/metrics.py")
trade = load("_lib.trade", "_lib/trade.py")
psim = load("portfolio_sim_for_t21_parity", "portfolio-sim.py")


def _t(day_utc, t0_ns, t1_ns, net_bps, notional_usd):
    return trade.from_row({
        "day_utc": day_utc, "t0_ns": t0_ns, "t1_ns": t1_ns,
        "net_bps": net_bps, "notional_usd": notional_usd,
    })


def test_daily_pnl_includes_zero_days_for_calendar_month():
    ns_day = 86_400 * 1_000_000_000
    t1 = dt_ns("2026-09-01") + 3600 * 1_000_000_000
    trades = [_t("2026-09-01", dt_ns("2026-09-01"), t1, 100, 1000)]
    daily = metrics.daily_pnl(trades, "2026-09")
    assert len(daily) == 23  # окно сентября canon: 01..23.09 (данные кончаются 24.09 00:00 UTC)
    assert daily["2026-09-01"] == 10.0
    assert daily["2026-09-02"] == 0.0  # сутки без сделок — не выпадают


def test_month_membership_is_by_signal_even_when_daily_groups_by_exit():
    """Сделка сигнала 31.08, выхода 01.09: месяц — август (canon §5 п.3), но её $ ложится в
    дневной P&L под 01.09 (день денег — `t1`, «хвост месяца»)."""
    t0 = dt_ns("2026-08-31") + 23 * 3600 * 1_000_000_000
    t1 = dt_ns("2026-09-01") + 600 * 1_000_000_000
    trades = [_t("2026-08-31", t0, t1, 100, 1000)]
    assert metrics.month_of_signal(trades[0]) == "2026-08"
    daily_aug = metrics.daily_pnl(trades, "2026-08")
    assert daily_aug["2026-09-01"] == 10.0  # хвост, добавлен отдельным ключом
    daily_sep = metrics.daily_pnl(trades, "2026-09")
    assert sum(daily_sep.values()) == 0.0  # в сентябрь сделка не принадлежит (сигнал — август)


def test_sharpe_n_days_reported_includes_zero_days():
    daily = {"2026-09-01": 10.0, "2026-09-02": 0.0, "2026-09-03": -5.0, "2026-09-04": 0.0}
    sharpe, n_days = metrics.sharpe_daily(daily)
    assert n_days == 4
    assert sharpe is not None


def test_sharpe_needs_at_least_two_days():
    sharpe, n_days = metrics.sharpe_daily({"2026-09-01": 10.0})
    assert sharpe is None and n_days == 1


def dt_ns(day: str) -> int:
    import datetime as dt
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1e9)


def test_drawdown_closed_matches_portfolio_sim_closed_drawdown():
    """Синтетическая серия закрытий: тот же результат из `_lib.metrics.drawdown_closed` (на
    `Trade`) и `portfolio_sim.closed_drawdown` (на исходных dict `taken`) — формула не менялась
    при переносе."""
    deposit = 2500.0
    series = [
        ("2026-09-01", 0 * 3600, 100, 500),     # +5.0
        ("2026-09-01", 1 * 3600, -400, 500),    # -20.0
        ("2026-09-01", 2 * 3600, -300, 500),    # -15.0 (просадка глубже)
        ("2026-09-01", 3 * 3600, 200, 500),      # +10.0 (частичное восстановление)
    ]
    taken = []
    trades = []
    day0 = dt_ns("2026-09-01")
    for day_utc, offset_s, net_bps, notional in series:
        t0 = day0 + offset_s * 1_000_000_000
        t1 = t0 + 300 * 1_000_000_000
        pnl = net_bps / 1e4 * notional
        taken.append({"t1": t1, "pnl": pnl})
        trades.append(_t(day_utc, t0, t1, net_bps, notional))

    dd_usd_ref, dd_pct_ref = psim.closed_drawdown(taken, deposit)
    dd_usd, dd_pct = metrics.drawdown_closed(trades, deposit)
    assert dd_usd == dd_usd_ref
    assert dd_pct == dd_pct_ref
