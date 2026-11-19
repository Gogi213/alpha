#!/usr/bin/env python3
"""TK-624: блок «Признак: успешные пробои монеты» (Эквити → Где мы) из итоговых таблиц TK-625 — только чтение.
Использование: python tk624_feature.py <step1-h775.json> <step1-o566.json> <ml_progress.json>
Источники: step1-*.json ступени 1 TK-625 (отчёт docs/findings/p42-successful-breakouts-2026-11-19.md, ветка TK-625 @ 68b8a9336, def2, code_id 3267c9a4e181);
копии лежат в docs/findings/p42-where-we-are-2026-11-19/tk625-step1-*.json. Берётся m = 1 NATR (колонки ok10_d7/d14/d30), корзины n_ok 0 / 1 / 2–3 / 4+ / нет значения.
Пишет feature.current.sections в ml_progress.json (строки уже отформатированы: запятая, «−», + у знакового). Проверка независимо: tk624_check.py.
Ставка 500 $, после комиссий, 01.07–13.11; Шарп — годовой по суточному ряду (дни без сделок = 0); срез сделок базы ≠ отдельная стратегия."""
import json
import sys

h775, o566, prog = sys.argv[1:4]
WINDOWS = [(7, "ok10_d7"), (14, "ok10_d14"), (30, "ok10_d30")]
BUCKETS = ["0", "1", "2–3", "4+", "нет значения"]
COLS = ["Окно", "Успешных пробоев", "Сделок", "Сумма, $", "На сделку, $", "Шарп (год)", "Июль, $", "Август, $", "Сентябрь, $", "Октябрь, $", "Ноябрь (1–13), $"]
SIDE = {"S": "Шорт", "L": "Лонг"}


def num(v, d=2, sign=False):
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


def row(win, label, b):
    return [win, label, str(b["n"]), num(b["sum"], 2, True), num(b["mean"], 2, True) if b["n"] else "—", num(b["sharpe"], 2, True)] + [num(m[1], 2, True) for m in b["months"]]


def tables(d):
    out = []
    for side in ("S", "L"):
        base = d[side]["base"]
        rows = [row("вся база", "без фильтра", base)]
        for dd, key in WINDOWS:
            for i, bk in enumerate(BUCKETS):
                rows.append(row(f"{dd} сут.", bk, d[side]["cols"][key][bk]))
        out.append({"title": f"{SIDE[side]} · база {num(base['sum'], 2, True)} $ на {base['n']} сделках, шарп {num(base['sharpe'], 2, True)}", "cols": COLS, "rows": rows, "bold": [0]})
    return out


H, O = json.load(open(h775, encoding="utf-8")), json.load(open(o566, encoding="utf-8"))
assert H["def"] == O["def"] == "def2" and H["code_id"] == O["code_id"] == "3267c9a4e181"
for d, (ns, nl) in ((H, (319, 428)), (O, (431, 621))):   # сделки баз: honest 775 — L 428 / S 319; 566 — L 621 / S 431
    assert d["S"]["base"]["n"] == ns and d["L"]["base"]["n"] == nl
    for side in ("S", "L"):
        for _, key in WINDOWS:   # корзины делят базу без остатка: сделки и сумма
            cols = d[side]["cols"][key]
            assert sum(cols[b]["n"] for b in BUCKETS) == cols["все"]["n"] == d[side]["base"]["n"]
            assert abs(sum(cols[b]["sum"] for b in BUCKETS) - d[side]["base"]["sum"]) < 1e-6

P = json.load(open(prog, encoding="utf-8"))
P["feature"]["current"] = {
    "title": "Итог TK-625 (20.11): «не ясно»",
    "status": "done",
    "text": "Не ясно. Ни одна клетка фильтра «торговать только монеты с не менее чем c успешными пробоями» не прошла поправку Холма на 9 проверок. Лучшая область — шорт, окно 14 суток, не меньше 2 успешных пробоев: +0,40 $ на сделку к базе (± 0,27), p = 0,0645 при пороге 0,0056. Ничего не заморожено; 17–30.11 не читалось.",
    "notes": [
        "Что считаем: «успешный пробой» — после пересечения уровня монеты цена ушла за него минимум на 1 NATR; признак — число таких пробоев этой монеты за последние 7 / 14 / 30 суток до сигнала (по вашему слову: не доля пробоев, а их количество). «Нет значения» — у монеты меньше этих суток истории или окно начинается до 24.06.",
        "Остальные 8 пробных клеток: у шорта 3 из 4 с приростом от +0,05 до +0,11 $ на сделку, у лонга 3 из 4 с приростом от −0,39 до −0,04, точность ± 0,12–0,46 $ на сделку. Область «шорт, 14–30 суток» слабо положительна, «лонг» — слабо отрицательна; ни то ни другое от нуля не отличимо.",
        "Для лонга сильнее видно обратное: самый доходный срез — монеты без успешных пробоев (окно 14 суток: 143 сделки, +166 $, +1,16 $ на сделку при +0,45 у базы; окно 30 суток: 40 сделок, +2,38 $ на сделку).",
        "Срез сделок базы — не отдельная стратегия: пауза и занятость портфеля не пересчитываются; таблицы описательные. Все числа — «найдено на этих данных».",
    ],
    "sections": [
        {"title": "Честная вселенная: 775 контрактов (TK-621) — решающая", "tables": tables(H)},
        {"title": "Прежние 566 монет — для сравнения", "fold": True, "tables": tables(O)},
    ],
}
json.dump(P, open(prog, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
print("ok:", len(P["feature"]["current"]["sections"]), "раздела,", sum(len(s["tables"]) for s in P["feature"]["current"]["sections"]), "таблиц")
