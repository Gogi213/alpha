#!/usr/bin/env python3
"""TK-638: блок «Титровка оборота и числа сделок» (Эквити → Где мы, «Признак: успешные пробои монеты», под раундом TK-636) + этап TK-638 в «Ход работ» из итогов юнита tk638-pnl.
Использование: python tk638_feature.py <каталог res (cells38-*.json, counts38-*.json)> <ml_progress.json>
Источник: res/cells38-<вс>-<сторона>.json юнита tk638-pnl (отчёт docs/findings/p42-turnover-grid-2026-11-20.md, ветка TK-638); копия — docs/findings/p42-turnover-grid-2026-11-20/res/.
Тепловые карты D × H (7 × 9 клеток на сторону, h775 — решающая; o566 — справочно, свёрнуто, + строка «без D»): Σ $, $ на сделку, Шарп (год), число сделок; лестницы числа сделок. Заливка — поле `shade` таблицы (TK-638).
Пишет feature.rounds (раунды TK-633 … TK-636 остаются, раунд TK-638 — под ними), этап TK-638, строку «Что дальше» и заголовок в ml_progress.json (идемпотентно). Без бп, без медиан."""
import json
import os
import sys

res_dir, prog = sys.argv[1:3]
D_LEVELS = (12, 25, 50, 100, 200, 300, 500)
H_LEVELS = (0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0, 5.0)
MONTHS = ["Июль, $", "Август, $", "Сентябрь, $", "Октябрь, $", "Ноябрь (1–13), $"]
SIDE = {"L": "Лонг", "S": "Шорт"}
R = {(u, s): json.load(open(os.path.join(res_dir, "cells38-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
CN = {(u, s): json.load(open(os.path.join(res_dir, "counts38-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}


def num(v, d=2, sign=False):
    if v is None or v != v:
        return "—"
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


def hh(h):
    return ("%g" % h).replace(".", ",")


def rows_of(u):
    return list(D_LEVELS) + (["none"] if u == "o566" else [])


def cell(u, s, d, h):
    return R[(u, s)]["pool"]["D%s_H%g" % (d, h) if d == "none" else "D%g_H%g" % (d, h)]


def heat(u, s, title, getter, fmt, shaded=True):
    vals = [[getter(cell(u, s, d, h)) for h in H_LEVELS] for d in rows_of(u)]
    mx = max(abs(v) for r in vals for v in r if v == v) or 1.0
    rows = [["без D" if d == "none" else str(d)] + [fmt(vals[ri][ci]) + ("*" if cell(u, s, d, h)["few"] else "") for ci, h in enumerate(H_LEVELS)] for ri, d in enumerate(rows_of(u))]
    t = {"title": f"{SIDE[s]} · {title} · по строкам суточный оборот D (млн $), по столбцам часовой H (млн $); * — меньше 70 сделок", "cols": ["D \\ H"] + [hh(h) for h in H_LEVELS], "rows": rows}
    if shaded:
        t["shade"] = [[None] + [v / mx for v in r] for r in vals]
    return t


def heats(u):
    out = []
    for s in "LS":
        out += [heat(u, s, "Σ $ (500 $ на сделку)", lambda c: c["sum"], lambda x: num(x, 0, True)),
                heat(u, s, "$ на сделку", lambda c: c["mean"], lambda x: num(x, 2, True)),
                heat(u, s, "Шарп (год)", lambda c: c["sharpe"], lambda x: num(x, 2, True)),
                heat(u, s, "число сделок", lambda c: c["n"], lambda x: str(int(x)), shaded=False)]
    return out


def label(c):
    return "%s ≥ %s сделок за %s (q%s)" % (c["lad"], "{:,}".format(int(c["N"])).replace(",", " "), "60 мин" if c["lad"] == "N60" else "24 ч", "/".join("%d" % round(p * 100) for p in c["ps"]))


def ladders(u):
    out = []
    for s in "LS":
        b = R[(u, s)]["base"]
        byc = {x["cid"]: x for x in CN[(u, s)]["ladders"]}
        cols = ["Ступень (абсолютное число сделок монеты на сигнале)", "Сделок", "Сумма, $", "На сделку, $", "Шарп (год)"] + MONTHS + ["R:R", "Стоп, % маржи"]

        def row(name, c):
            st = c["rr"]["stops"]
            return [name, str(c["n"]) + (" (мало)" if c["few"] else ""), num(c["sum"], 1, True), num(c["mean"], 2, True), num(c["sharpe"], 2, True)] + [num(m[1], 0, True) for m in c["months"]] + [num(c["rr"]["rr"]), num(-st["mean_loss_pct"], 1) if st["n"] else "нет стопов"]
        rows = [row("вся база (без фильтра)", b)] + [row(label(byc[k]), c) for k, c in R[(u, s)]["ladders"].items()]
        out.append({"title": f"{SIDE[s]} · число сделок монеты · база {num(b['sum'], 2, True)} $ на {b['n']} сделках, шарп {num(b['sharpe'], 2, True)}", "cols": cols, "rows": rows, "bold": [0], "wrap": [0]})
    return out


bh = {s: R[("h775", s)]["base"] for s in "LS"}
lad = {s: R[("h775", s)]["ladders"] for s in "LS"}
bl, bs = lad["L"]["N24_q60"], lad["S"]["N24_q20"]
assert bl["n"] == 181 and abs(bl["sum"] - 294.3) < 0.1 and abs(bs["sum"] - 245.0) < 0.1, (bl["n"], bl["sum"], bs["sum"])
L12, L25, L50, L200 = (cell("h775", "L", d, 0.5) for d in (12, 25, 50, 200))
S_all = [cell("h775", "S", d, h) for d in D_LEVELS for h in H_LEVELS]
assert all(c["sum"] > 0 for c in S_all)

ROUND = {
    "title": "Титровка оборота и числа сделок (TK-638, 20.11): описательно",
    "status": "done",
    "text": (f"Описательная таблица, без вердикта. Суточный оборот выше базового 12 млн $: у лонга сумма падает ({num(bh['L']['sum'], 0, True)} → {num(L50['sum'], 0, True)} → {num(L200['sum'], 0, True)} $ при 12 → 50 → 200 млн $), "
             f"у шорта плюс во всех 63 клетках (Σ {num(min(c['sum'] for c in S_all), 0)}…{num(max(c['sum'] for c in S_all), 0)} $). Часовой оборот ниже 0,5 млн $ почти ничего не добавляет. "
             f"Число сделок монеты за сутки заметнее оборота: лонг, не меньше 220 000 сделок за 24 ч — {bl['n']} сделок, {num(bl['sum'], 0, True)} $ (шарп {num(bl['sharpe'], 2)}) против базы {num(bh['L']['sum'], 0, True)} $ (шарп {num(bh['L']['sharpe'], 2)}); "
             f"найдено на этих данных (отбор из 71 клетки на сторону), шум по такой сетке даёт шарп до 4,0 — кандидат, не результат."),
    "notes": [
        "Что считаем (слова владельца: «у меня была идея протитровать дейли вольюм до 500 млн / часовой тоже как то потитровать / для прикола еще можно взять количество сделок но мне кажется это не будет особо влиять но попробовать надо»; «у часового оборота шаг уменьши мне кажется это важно»): "
        "сетка суточный оборот D ∈ {12, 25, 50, 100, 200, 300, 500} млн $ × часовой оборот H ∈ {0,25; 0,35; 0,5; 0,7; 1; 1,4; 2; 3; 5} млн $ = 63 клетки на сторону (торгуем, только если оборот на сигнале не ниже D и H), плюс 8 ступеней числа сделок монеты за 60 мин и за 24 ч. Стоп и выходы базы не менялись.",
        "Число сделок — один абсолютный порог на все монеты (общий квантиль 20 / 40 / 60 / 80 % сигналов базы h775 по зачётным диапазонам, округлён до 2 значащих цифр; те же числа на 566 монетах); окна — минуты строго раньше сигнала, как у ключей движка.",
        f"Как читать: по областям, а не по лучшей клетке; все клетки показаны, клетки меньше 70 сделок помечены «*». Испытаний для поправки Холма 0 (описательная сетка); просмотрено 71 клетка на сторону. R:R и убыток стопа — в таблицах: база лонг R:R {num(bh['L']['rr']['rr'])}, стоп {num(bh['L']['rr']['stops']['mean_loss'])} $ = {num(-bh['L']['rr']['stops']['mean_loss_pct'], 1)} % маржи при плече 2; шорт R:R {num(bh['S']['rr']['rr'])}, {num(bh['S']['rr']['stops']['mean_loss'])} $ = {num(-bh['S']['rr']['stops']['mean_loss_pct'], 1)} %.",
        f"Число сделок и оборот связаны (корреляция рангов на кандидатах лонга 0,49), но разные: против клетки D = 50 в лонге остаются 61 сделка только по числу сделок (+146 $) и 71 только по обороту (−52 $). Шорт: N24 ≥ 78 000 даёт {num(bs['sum'], 0, True)} $ (шарп {num(bs['sharpe'], 2)}) против базы {num(bh['S']['sum'], 0, True)} $ ({num(bh['S']['sharpe'], 2)}), выше порога сумма ниже базы.",
        "Ворота пройдены до чтения P&L и после: число сделок Rust = независимый пересчёт (298 кандидатов, 0 расхождений); G8 — счётчики фильтра движка = пересчёту (48 + 48 прогонов); G-base — клетка D12·H0,5 = база по списку и P&L. Критик — PASS до чтения P&L.",
    ],
    "sections": [
        {"title": "Честная вселенная: 775 контрактов (TK-621) — решающая: тепловые карты D × H", "note": "База h775 — D = 12, H = 0,5 (клетка «12 / 0,5»). Заливка — по знаку и модулю в своей таблице: акцентный цвет — плюс, серый — минус. Клетки с H < 0,5 расширяют пул базы.", "tables": heats("h775")},
        {"title": "Честная вселенная: число сделок монеты за 60 мин и за 24 ч (лестницы)", "note": "Один абсолютный порог на все монеты; сигнал с числом сделок ниже порога не торгуется (клетка пропускает при «пусто» — таких нет). Первая строка — вся база.", "tables": ladders("h775")},
        {"title": "Прежние 566 монет — для сравнения (в базе нет порога суточного оборота: строка «без D» = база)", "fold": True, "tables": heats("o566") + ladders("o566")},
    ],
}

STAGE = {
    "id": "TK-638", "title": "Титровка оборота (суточный × часовой) и числа сделок монеты", "status": "done", "group": "Сделано 20.11",
    "answers": "Как меняются деньги, если торговать только при суточном обороте не ниже D и часовом не ниже H, и если требовать минимум числа сделок монеты — отдельно лонг и шорт.",
    "numbers": [f"Описательно, без вердикта: 63 клетки D × H + 8 ступеней числа сделок на сторону; суточный оборот выше 12 млн $ лонг не улучшает ({num(bh['L']['sum'], 0, True)} → {num(L50['sum'], 0, True)} $ при D = 50), шорт плюс во всех клетках.",
                f"Число сделок за 24 ч заметнее оборота: лонг, не меньше 220 000 сделок за сутки — {bl['n']} сделок, {num(bl['sum'], 0, True)} $ (шарп {num(bl['sharpe'], 2)}) против базы {num(bh['L']['sum'], 0, True)} $ (шарп {num(bh['L']['sharpe'], 2)}); найдено на этих данных, шум по сетке даёт шарп до 4,0.",
                "Таблицы и тепловые карты — в блоке «Титровка оборота и числа сделок» под таблицами ниже."],
    "next": "Продолжение (проверка вперёд числа сделок за сутки) — только записью координатора и условиями критика.",
}

P = json.load(open(prog, encoding="utf-8"))
rounds = [r for r in P["feature"].get("rounds", []) if "TK-638" not in r["title"]]
assert len(rounds) == 3 and rounds[2]["title"].startswith("Монета как признак (TK-636,"), "ожидались раунды TK-633, TK-634, TK-636"
P["feature"]["rounds"] = rounds + [ROUND]
P["stages"] = [s for s in P["stages"] if s["id"] != "TK-638"]
i = next(k for k, s in enumerate(P["stages"]) if s["id"] == "TK-636")
P["stages"].insert(i, STAGE)
old_head = "20.11: закрыты TK-636 —"
new_head = "20.11: закрыты TK-638 — титровка оборота и числа сделок монеты (описательные таблицы, без вердикта); TK-636 —"
if "TK-638" not in P["headline"]:
    assert old_head in P["headline"]
    P["headline"] = P["headline"].replace(old_head, new_head, 1)
nx = P["where"]["next"]["items"][0]
add = " Титровка оборота и числа сделок (TK-638) — тепловые карты D × H и лестницы в блоке ниже; число сделок за сутки у лонга заметнее оборота, найдено на этих данных, проверка вперёд — только записью координатора."
if "TK-638" not in nx["s"]:
    nx["s"] = nx["s"] + add
json.dump(P, open(prog, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
print("ok: таблиц", sum(len(sec["tables"]) for sec in ROUND["sections"]), "строк", sum(len(t["rows"]) for sec in ROUND["sections"] for t in sec["tables"]))
