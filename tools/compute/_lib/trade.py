#!/usr/bin/env python3
"""Каноническая строка сделки счёта (T-21: `docs/findings/t21-metrics-canon-2026-09-27.md` §5,
условие 3 Судьи в `docs/research/reviews/t21-canon-2026-09-27.md`).

Поля `Trade`: `symbol, day_utc, month, t0_ns, t1_ns, net_bps, notional_usd, qty, entry_vwap,
exit_px, fill_frac, reason, fee_bps, funding_usd, dir, form, set`; `pnl_usd` — вычисляемое
свойство: `net_bps/1e4 × notional_usd − funding_usd` (фандинг отдельно от комиссии — условие 3;
комиссия уже внутри `net_bps`, В-63). `month` — производная от `day_utc`/`t0` (сутки СИГНАЛА, не
выхода `t1`; решение Судьи, У4 п.3 canon §5): месяц — это разбиение данных для решения, принятого
в момент входа.

`from_row` принимает синонимы полей, найденные ревизией T-21 (canon §1, таблица «колонки строки»):
symbol/sym; t0_ns/t0/t0_ms; t1_ns/t1/exit_ns/exit_ms; net_bps/net; entry_vwap/entry_px/entry;
fill_frac/fill; в СТРОКЕ СДЕЛКИ `usd`/`notional_usd` = NOTIONAL (позиция), не pnl (В-93) — читается
как синоним notional, не пути к pnl. Единицы времени времени определяются по ИМЕНИ поля (суффикс
`_ms`/`ms` → миллисекунды → ×1e6 в нс), не по величине числа."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

NS_PER_MS = 1_000_000

_T0_SYNONYMS = ["t0_ns", "t0", "t0_ms"]
_T1_SYNONYMS = ["t1_ns", "t1", "exit_ns", "exit_ms"]
_NET_SYNONYMS = ["net_bps", "net"]
_PNL_SYNONYMS = ["pnl_usd", "pnl", "total_usd", "net_usd"]
_ENTRY_SYNONYMS = ["entry_vwap", "entry_px", "entry"]
_FILL_SYNONYMS = ["fill_frac", "fill"]
_NOTIONAL_SYNONYMS = ["notional_usd", "usd"]  # "usd" строки сделки — В-93, НЕ pnl


def day_utc_of(t_ns: int) -> str:
    """Сутки UTC метки времени в наносекундах (`YYYY-MM-DD`), как `portfolio-sim.day_of`."""
    return dt.datetime.fromtimestamp(t_ns / 1_000_000_000, dt.timezone.utc).strftime("%Y-%m-%d")


def month_of_day(day_utc: str) -> str:
    """`YYYY-MM` дня (день уже посчитан по сигналу — решение Судьи, У4 п.3)."""
    return day_utc[:7]


@dataclass
class Trade:
    symbol: str
    day_utc: str
    t0_ns: int
    t1_ns: int
    net_bps: float
    notional_usd: float
    qty: float | None = None
    entry_vwap: float | None = None
    exit_px: float | None = None
    fill_frac: float = 1.0
    reason: str = ""
    fee_bps: float | None = None
    funding_usd: float = 0.0
    dir: int = 1
    form: str = ""
    set: str = ""
    month: str = field(default="")

    def __post_init__(self):
        if not self.month:
            self.month = month_of_day(self.day_utc)

    @property
    def pnl_usd(self) -> float:
        """`net_bps/1e4 × notional_usd − funding_usd` (условие 3 Судьи: фандинг виден отдельно
        от комиссии, комиссия — уже внутри `net_bps`, В-63)."""
        return self.net_bps / 1e4 * self.notional_usd - self.funding_usd


def _to_ns(value, field_name: str) -> int:
    v = int(value)
    if field_name.endswith("_ms") or field_name == "ms":
        return v * NS_PER_MS
    return v


def _first_field(row: dict, names: list[str]) -> str | None:
    for n in names:
        if row.get(n) not in (None, ""):
            return n
    return None


def _as_float(v) -> float | None:
    return None if v in (None, "") else float(v)


def from_row(row: dict) -> Trade:
    """Строка любого известного источника (`rounds.csv`, T-32 `main-trades.csv`, …) → `Trade`.
    Отсутствие `t0`/`t1` под любым синонимом — `KeyError` (нет времени сделки — не сделка)."""
    symbol = row.get("symbol") or row.get("sym")
    t0_field = _first_field(row, _T0_SYNONYMS)
    t1_field = _first_field(row, _T1_SYNONYMS)
    if t0_field is None or t1_field is None:
        raise KeyError(
            f"t0/t1 не найдены (искал {_T0_SYNONYMS} / {_T1_SYNONYMS}) среди полей {sorted(row)}")
    t0_ns = _to_ns(row[t0_field], t0_field)
    t1_ns = _to_ns(row[t1_field], t1_field)
    day_utc = row.get("day_utc") or day_utc_of(t0_ns)

    net_field = _first_field(row, _NET_SYNONYMS)
    net_bps = float(row[net_field]) if net_field else 0.0

    qty = _as_float(row.get("qty"))
    entry_field = _first_field(row, _ENTRY_SYNONYMS)
    entry_vwap = _as_float(row[entry_field]) if entry_field else None

    notional_field = _first_field(row, _NOTIONAL_SYNONYMS)
    notional = _as_float(row[notional_field]) if notional_field else None
    if notional is None and qty is not None and entry_vwap is not None:
        notional = qty * entry_vwap
    funding_usd = _as_float(row.get("funding_usd")) or 0.0
    if notional is None:
        # Источник без qty/entry/usd (агрегат под видом строки сделки) — раз есть готовый pnl,
        # notional восстанавливается из него самого через net_bps, иначе pnl_usd (свойство) не
        # совпадёт с тем, что источник имел в виду. При net_bps == 0 восстановить нечем — 0.0.
        pnl_field = _first_field(row, _PNL_SYNONYMS)
        if pnl_field and net_bps:
            notional = (float(row[pnl_field]) + funding_usd) * 1e4 / net_bps
        else:
            notional = 0.0

    fill_field = _first_field(row, _FILL_SYNONYMS)
    fill_frac = _as_float(row[fill_field]) if fill_field else None
    if fill_frac is None:
        fill_frac = 1.0

    dir_raw = row.get("dir")
    dir_val = int(dir_raw) if dir_raw not in (None, "") else 1

    return Trade(
        symbol=symbol, day_utc=day_utc, t0_ns=t0_ns, t1_ns=t1_ns, net_bps=net_bps,
        notional_usd=notional, qty=qty, entry_vwap=entry_vwap,
        exit_px=_as_float(row.get("exit_px")), fill_frac=fill_frac,
        reason=row.get("reason", ""), fee_bps=_as_float(row.get("fee_bps")),
        funding_usd=funding_usd, dir=dir_val, form=row.get("form", ""), set=row.get("set", ""),
    )
