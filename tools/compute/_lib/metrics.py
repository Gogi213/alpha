#!/usr/bin/env python3
"""Канонические метрики счёта (T-21 `docs/findings/t21-metrics-canon-2026-09-27.md` §2/§5, решения
Судьи `docs/research/reviews/t21-canon-2026-09-27.md`).

`day_of_exit(t1_ns)` — сутки UTC выхода: ось времени ВНУТРИ месяца (кривая, дневной P&L, худшие
сутки, Шарп — канон §5 п.3: «время внутри месяца — по `t1`»).

`month_of_signal(trade)` — месяц сделки = месяц её СИГНАЛА (`day_utc`/`t0`), НЕ `t1` (решение
Судьи, canon §5 п.3: сделка 31.08 23:50 → 01.09 — августовская; месяц — разбиение данных для
решения, принятого на входе).

`daily_pnl(trades, month)` — дневной P&L по `t1` UTC сделок месяца `month` (принадлежность —
по сигналу), за КАЛЕНДАРНЫЕ сутки месяца, включая нулевые (Судья: «бот стоит и в нулевые дни»).
Хвост месяца по `t1` может дать сутки за пределами календаря месяца (сделка входа в последний
день месяца, выход на следующий) — такие сутки добавляются отдельным ключом, не теряются и не
искажают календарь месяца (canon §5 п.3: «это нормально»).

`sharpe_daily(daily)` — (Шарп, число суток): среднее/σ×√365, σ — `statistics.pstdev` (population,
ddof=0), КАК В `titration-dashboard-merge.sharpe_sortino` (проверено построчно, см. отчёт T-21).
Число суток — рядом с числом (Судья: «Шарп месяца — описание, без интервала варианты не
сравнивать»).

`drawdown_closed(trades, deposit)` — «откат по закрытиям» (П2 canon §2/§5) — порт БЕЗ ИЗМЕНЕНИЯ
ФОРМУЛЫ из `portfolio-sim.closed_drawdown` (растёт только на закрытии сделки по `t1`, без
переоценки открытых позиций между сделками).

«Просадка счёта» (П1, главная владельцу) — минутная переоценка открытых позиций — в этом шаге НЕ
портируется: `portfolio-sim.minute_curve` тянет `Klines`/`Funding` (свечи, фандинг) и группировку
одновременных событий по минуте — конвейер тяжелее, чем можно вынести без переноса самих `Klines`/
`Funding`. Сигнатура для переноса ПОЗЖЕ (Судья, canon §3 п.5: «переход по одному с гейтом
"до = после"», это следующий шаг, не T-21-«_lib-пакетом»):

    minute_curve(taken: list[dict], klines: Klines, deposit: float, total: float)
        -> dict с dd_usd, dd_pct, rf, rec_days, unrecovered, n_no_klines, peak_usd, ...
        (portfolio-sim.py:246-316; taken — список dict с t0/t1/sym/net/pnl/entry/dir/fee/usd/fill,
        не `Trade` — переносить формат заодно с функцией, не раньше)."""
from __future__ import annotations

import datetime as dt
import statistics as st

# Окна месяца (canon §5 п.3, числа — из `kpi-newhigh.PERIODS`): конец сентября — 2026-09-24
# 00:00 UTC, НЕ включая (данные кончаются раньше конца календарного месяца, не 30.09).
MONTH_WINDOWS = {
    "aug": ("2026-08-01", "2026-09-01"),
    "sep": ("2026-09-01", "2026-09-24"),
}


def day_of_exit(t1_ns: int) -> str:
    return dt.datetime.fromtimestamp(t1_ns / 1_000_000_000, dt.timezone.utc).strftime("%Y-%m-%d")


def _field(t, name: str):
    return getattr(t, name) if hasattr(t, name) else t[name]


def month_of_signal(trade) -> str:
    """Месяц = месяц `day_utc` сделки (сигнал `t0`), не `t1` (решение Судьи, canon §5 п.3)."""
    day_utc = _field(trade, "day_utc")
    return day_utc[:7]


_WINDOW_BY_MONTH = {start[:7]: (start, end) for start, end in MONTH_WINDOWS.values()}


def _calendar_days(month: str) -> list[str]:
    """Календарные сутки месяца — обычно весь месяц, но для месяцев из `MONTH_WINDOWS` (сейчас
    только сентябрь) — до конца ДАННЫХ, не до 30/31 числа (canon: «конец сентября — 2026-09-24
    00:00 UTC, не включая» — сутки после него не существуют, не то что «нулевые»)."""
    if month in _WINDOW_BY_MONTH:
        start_s, end_s = _WINDOW_BY_MONTH[month]
        start, end = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
    else:
        year, mon = int(month[:4]), int(month[5:7])
        start = dt.date(year, mon, 1)
        end = dt.date(year + (1 if mon == 12 else 0), 1 if mon == 12 else mon + 1, 1)
    out, d = [], start
    while d < end:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def daily_pnl(trades, month: str) -> dict:
    """Дневной P&L (по `t1` UTC) сделок месяца `month` (принадлежность — по сигналу, canon §5
    п.3); ключи — календарные сутки месяца (включая нулевые); сутки `t1` за пределами календаря
    месяца (хвост) добавляются отдельным ключом, а не отбрасываются."""
    out = {d: 0.0 for d in _calendar_days(month)}
    for t in trades:
        if month_of_signal(t) != month:
            continue
        day = day_of_exit(_field(t, "t1_ns"))
        pnl = t.pnl_usd if hasattr(t, "pnl_usd") else t["pnl_usd"]
        out[day] = out.get(day, 0.0) + pnl
    return out


def sharpe_daily(daily: dict, deposit_usd: float | None = None, annualize: int = 365):
    """(Шарп, число суток `n_days`). Без `deposit_usd` — Шарп по ДОЛЛАРУ (варианты с разным
    депозитом так не сравнивать); с `deposit_usd` — по % депозита в сутки (canon). σ —
    `statistics.pstdev` (population, ddof=0) — как `titration-dashboard-merge.sharpe_sortino`."""
    vals = list(daily.values())
    n_days = len(vals)
    if deposit_usd:
        vals = [v / deposit_usd * 100 for v in vals]
    if n_days < 2:
        return None, n_days
    mean = st.mean(vals)
    std = st.pstdev(vals)
    if std <= 0:
        return None, n_days
    return round(mean / std * (annualize ** 0.5), 2), n_days


def drawdown_closed(trades, deposit: float):
    """«Откат по закрытиям» (П2) — порт формулы `portfolio-sim.closed_drawdown` без изменений:
    капитал растёт только на закрытии сделки (шаг по `t1`), без переоценки открытых позиций между
    сделками. Возвращает `(max_dd_usd, max_dd_pct)`."""
    eq = peak = deposit
    max_dd = max_dd_pct = 0.0
    for t in sorted(trades, key=lambda x: _field(x, "t1_ns")):
        pnl = t.pnl_usd if hasattr(t, "pnl_usd") else t["pnl_usd"]
        eq += pnl
        if eq >= peak:
            peak = eq
        else:
            max_dd = max(max_dd, peak - eq)
            max_dd_pct = max(max_dd_pct, (peak - eq) / peak * 100 if peak else 0.0)
    return max_dd, max_dd_pct
