#!/usr/bin/env python3
"""Формирование пар П-32 за сменным интерфейсом (TK-484, указание CEO 03.11 после В-91).

Метод отбора пар и оценка параметров спреда будут пересмотрены (П-32 v4), поэтому драйвер знает только
интерфейс: `form(ctx) -> [Selected]`. Модуль регистрируется в `FORMATIONS`; выбор — флагом драйвера.

ctx — словарь: exchange; month ('YYYY-MM'); form_from_day, form_to_day (90 суток до месяца, сутки от
2020-01-01 UTC, to не включая); trade_from_day, trade_to_day; alive — множество символов вселенной
биржи клетки, живых хотя бы в один день торгового месяца (снятые остаются до дня снятия); alive_all —
то же по каждой бирже; options — словарь
строк из `--formation-opt k=v`. Строго по прошлому: модуль смотрит только окно формирования.

Selected = (y, [x, ...]) — символ ноги Y и символы ноги X (корзина — несколько, равные веса).
Заглушки (`stub = True`) нужны для проверки конвейера, их результат вердиктом не считается.
"""
import csv

Selected = tuple  # (y: str, x: list[str])


def fixed_pairs(ctx):
    """Заглушка: пары из csv `y,x` (x — символы через `+`), одни и те же в каждом месяце.

    Пара берётся, если все её символы живы в торговом месяце; никакой статистики на формировании нет.
    """
    path = ctx["options"]["pairs"]
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            y, x = r["y"].strip(), [s.strip() for s in r["x"].split("+")]
            if y in ctx["alive"] and all(s in ctx["alive"] for s in x):
                out.append((y, x))
    return out


fixed_pairs.stub = True


def anchor_leg(ctx):
    """Заглушка A2: все живые символы биржи против одного якоря (`anchor=BTCUSDT`) без отбора."""
    a = ctx["options"]["anchor"]
    if a not in ctx["alive"]:
        return []
    return [(s, [a]) for s in sorted(ctx["alive"]) if s != a]


anchor_leg.stub = True


def same_coin(ctx):
    """Одна монета на двух биржах (C1/D2): пары из таблицы сопоставления ворот §4 (`accepted=1`).

    Не статистика формирования, а тождество монеты; обе ноги должны быть живы в торговом месяце
    (`alive_all[биржа]`). Опции: `symmap` — csv ворот, `exch_a`, `exch_b`.
    """
    o = ctx["options"]
    ya, xb = ctx["alive_all"][o["exch_a"]], ctx["alive_all"][o["exch_b"]]
    out = []
    with open(o["symmap"], newline="") as f:
        for r in csv.DictReader(f):
            if (r["accepted"] == "1" and r["exch_a"] == o["exch_a"] and r["exch_b"] == o["exch_b"]
                    and r["sym_a"] in ya and r["sym_b"] in xb):
                out.append((r["sym_a"], [r["sym_b"]]))
    return out


FORMATIONS = {"fixed": fixed_pairs, "anchor": anchor_leg, "same_coin": same_coin}


def get(name):
    try:
        return FORMATIONS[name]
    except KeyError:
        raise SystemExit(f"формирование {name!r} не зарегистрировано: {sorted(FORMATIONS)}")
