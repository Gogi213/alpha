#!/usr/bin/env python3
"""TK-636: блок «Монета как признак» (Эквити → Где мы, «Признак: успешные пробои монеты», под раундом TK-634) + этап TK-636 в «Ход работ» из итогов TK-636 — только чтение.
Использование: python tk636_feature.py <select36.json> <ml_progress.json>
Источник: res/select36.json юнита tk636-pnl (отчёт docs/findings/p42-coin-as-feature-2026-11-20.md, ветка TK-636, def2, code_id 519f3a173dc3);
копия — docs/findings/p42-coin-as-feature-2026-11-20/select36.json (рядом counts36-h775-<L|S>.json — «g5 по построению»). Пробные клетки честной вселенной h775 (775 контрактов, t24 = 12): сторона × клетка —
32 строки, каждая клетка показана, лучшая не выбирается; лесенка суточного оборота (просьба владельца) — отдельной таблицей и на o566 (справочно, свёрнуто).
Пишет feature.rounds (раунды TK-633 и TK-634 остаются, раунд TK-636 — под ними), этап TK-636, строку «Что дальше» и заголовок в ml_progress.json (идемпотентно). Ставка 500 $, после комиссий, 01.07–13.11; Шарп — годовой по суточному ряду."""
import json
import os
import re
import sys

sel_path, prog = sys.argv[1:3]
S = json.load(open(sel_path, encoding="utf-8"))
assert S["def"] == "def2" and S["code_id"] == "519f3a173dc3", (S["def"], S["code_id"])
assert S["holm"]["m_joint"] == 61 and S["holm"]["m"] == 32 and S["passed"] == [] and S["repl"] == {}, "ожидался исход отчёта: m = 61, ни одна клетка не прошла g1"
OUTCOME = {k: v[0] for k, v in S["outcomes"].items()}
assert OUTCOME == {"лонг": "не ясно", "шорт": "не ясно"}, OUTCOME
TOV = ("TOV_16", "TOV_24", "TOV_36", "TOV_54", "TOV_80")
SIDE = {"L": "Лонг", "S": "Шорт"}
WIN = {"15m": "15 мин", "1h": "1 ч", "4h": "4 ч", "24h": "24 ч"}
MON = ["Июль, $", "Август, $", "Сентябрь, $", "Октябрь, $", "Ноябрь (1–13), $"]


def num(v, d=2, sign=False):
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


def label(cid):
    m = re.fullmatch(r"TOV_(\d+)", cid)
    if m:
        return f"оборот за сутки ≥ {m.group(1)} млн $"
    if cid == "AGE":
        return "возраст листинга: без самых свежих 25 %"
    if cid == "VOL":
        return "волатильность: без самых тихих 25 %"
    if cid == "PEER":
        return "соседи по кластеру: без «одиночных» пробоев (15 мин)"
    m = re.fullmatch(r"COUP(\w+)_(hi|lo)", cid)
    assert m, cid
    return f"связь с рынком ({WIN[m.group(1)]}): без самых " + ("привязанных 25 %" if m.group(2) == "hi" else "оторванных 25 %")


def reach(side):
    c = json.load(open(os.path.join(os.path.dirname(os.path.abspath(sel_path)), f"counts36-h775-{side}.json"), encoding="utf-8"))
    adm = {x["cid"]: x["admit_floor"] for x in c["cells"]}
    return {cid: sum(1 for nb, j in c["jaccard"][cid].items() if adm.get(nb) and j <= 0.8) >= 2 for cid in TOV}


def table(slices_key, rows_key, cids, with_g5, title):
    tabs = []
    for side in "LS":
        sl, rw = S[slices_key][side], S[rows_key][side]
        b = sl["база"]
        cols = ["Клетка (торгуем без исключённого)", "Сделок", "Сумма, $", "На сделку, $", "Шарп (год)"] + MON + ["Δ к базе, $ на сделку (± ошибка)", "p"]
        if with_g5:
            cols.append("Плато (g5) достижимо по построению")
        out = [["вся база (без фильтра)", str(b["n"]), num(b["sum"], 2, True), num(b["mean"], 2, True), num(b["sharpe"], 2, True)] + [num(s, 2, True) for _, s in b["months"]] + ["—", "—"] + (["—"] if with_g5 else [])]
        rc = reach(side) if with_g5 else {}
        for cid in cids:
            r, v = rw[cid], sl[cid]
            out.append([label(cid), str(v["n"]), num(v["sum"], 2, True), num(v["mean"], 2, True), num(v["sharpe"], 2, True)] + [num(s, 2, True) for _, s in v["months"]]
                       + [f"{num(r['d'], 2, True)} ± {num(r['se'])}", (num(r["p"], 4) if r.get("p") is not None else "—")] + ([("да" if rc[cid] else "нет")] if with_g5 else []))
        tabs.append({"title": f"{SIDE[side]} · {title} · база {num(b['sum'], 2, True)} $ на {b['n']} сделках, шарп {num(b['sharpe'], 2, True)}", "cols": cols, "rows": out, "bold": [0], "wrap": [0]})
    return tabs


OTHER = [c for c in S["rows"]["L"] if S["rows"]["L"][c] and S["rows"]["L"][c]["trial"] and c not in TOV]
pmin = min(S["holm"]["ps"])
THR = 0.05 / 61
best = S["rows"]["S"]["COUP1h_hi"]
bsl = S["slices_h775"]["S"]["COUP1h_hi"]
assert best["p"] == pmin and best["d"] > 0 and not best["g1"]
sums_lower = all(S["slices_h775"][sd][c]["sum"] < S["slices_h775"][sd]["база"]["sum"] for sd in "LS" for c in TOV)
assert sums_lower      # текст ниже: сумма $ ниже базы на всех ступенях лесенки обеих сторон
ladder = {sd: [S["slices_h775"][sd][c]["sum"] for c in TOV] for sd in "LS"}
base = {sd: S["slices_h775"][sd]["база"]["sum"] for sd in "LS"}
dpos = [(sd, c) for sd in "LS" for c in TOV if S["rows"][sd][c]["d"] > 0]

ROUND = {
    "title": "Монета как признак (TK-636, 20.11): «не ясно»",
    "status": "done",
    "text": (f"Не ясно — и лонг, и шорт. Ни одна из 32 новых клеток не прошла поправку Холма (61 проверка: 29 прежних и 32 новых): лучший p = {num(pmin, 4)} при пороге {num(THR, 4)} "
             f"(шорт, связь с рынком за 1 ч: {bsl['n']} сделок, {num(bsl['sum'], 0, True)} $ против базы {S['slices_h775']['S']['база']['n']} сделок, {num(base['S'], 0, True)} $). Ничего не заморожено; сутки после 13.11 не читались."),
    "notes": [
        "Что считаем (слова владельца: «так возьми монеты как признак ало», «ты признаки корреляций все прогнал?», «За 7 дней много. Нужно за разные периоды внутри дня»): к базе добавлен фильтр по признаку монеты — класс (крипто / акции и сырьё; справочно), суточный оборот, возраст листинга, волатильность, связь монеты с рынком в окнах 15 мин / 1 ч / 4 ч / 24 ч (оба направления), число соседей по кластеру с пробоем за последние 15 минут. Клеток 16 на сторону.",
        f"Суточный оборот — по просьбе владельца перебор при фиксированном часовом обороте (0,5 млн $): торгуем только монеты с суточным оборотом не ниже порога 16 / 24 / 36 / 54 / 80 млн $. Сумма в долларах ниже базы на всех пяти ступенях обеих сторон (лонг {' / '.join(num(x, 0) for x in ladder['L'])} $ против {num(base['L'], 0)} $; шорт {' / '.join(num(x, 0) for x in ladder['S'])} $ против {num(base['S'], 0)} $); прирост на сделку положителен только у {len(dpos)} из 10 ступеней и от нуля не отличим. Плато (проверка на устойчивость к соседним порогам) по построению могли пройти только 24 и 36 млн $ — не прошли.",
        "Критик: на прежних 566 монетах нет порога 12 млн $ суточного оборота, поэтому повтор оборота там по построению близок к подтверждению (тот же эффект, что уже видели в TK-621) — это не независимая проверка. «Связь с рынком» и «соседи по кластеру» — связь с широкой группой рынка (при выбранном срезе один крупный кластер на 55–205 монет), не сектор.",
        f"Лучшая область — шорт, связь с рынком за 1 ч: {num(best['d'], 2, True)} ± {num(best['se'])} $ на сделку к базе при Шарпе {num(bsl['sharpe'], 2)} против {num(S['slices_h775']['S']['база']['sharpe'], 2)}; на прежних 566 монетах та же клетка не лучше базы. Все числа — «найдено на этих данных».",
    ],
    "sections": [
        {"title": "Честная вселенная: 775 контрактов (TK-621) — решающая", "note": "Суточный оборот — лесенка из пяти ступеней, все показаны. База — те же сделки без фильтра. Ставка 500 $, 01.07–13.11.",
         "tables": table("slices_h775", "rows", TOV, True, "суточный оборот"), },
        {"title": "Честная вселенная: остальные клетки (возраст, волатильность, связь с рынком, соседи по кластеру)", "note": "Все 11 клеток на сторону; лучшая не выбирается.",
         "tables": table("slices_h775", "rows", OTHER, False, "остальные признаки")},
        {"title": "Прежние 566 монет — для сравнения (суточный оборот без порога 12 млн $ в базе)", "fold": True, "tables": table("slices_o566", "rows_o566", TOV, False, "суточный оборот")},
    ],
}

STAGE = {
    "id": "TK-636", "title": "Монета как признак (оборот, возраст, волатильность, связь с рынком, соседи по кластеру)", "status": "done", "group": "Сделано 20.11",
    "answers": "Помогает ли отбирать сделки по признакам самой монеты — суточный оборот, возраст листинга, волатильность, связь с рынком в окнах внутри суток, соседи по кластеру?",
    "numbers": [f"Не ясно — и лонг, и шорт: ни одна из 32 новых клеток не прошла поправку Холма (61 проверка, лучший p = {num(pmin, 4)} при пороге {num(THR, 4)}).",
                "Суточный оборот (перебор 16–80 млн $): сумма в долларах ниже базы на всех ступенях обеих сторон; таблица в $ и шарпе — вкладка «Эквити», вид «Где мы», блок «Признак: успешные пробои монеты»."],
    "next": "Закрыто: ничего не заморожено. Другая форма признака — только новой записью координатора.",
}

P = json.load(open(prog, encoding="utf-8"))
rounds = [r for r in P["feature"].get("rounds", []) if "TK-636" not in r["title"]]
assert len(rounds) == 2 and rounds[1]["title"].startswith("Импульс и поток сделок на истории пробоев (TK-634,"), "ожидались только раунды TK-633 и TK-634"
P["feature"]["rounds"] = rounds + [ROUND]          # TK-636 — под блоками TK-633 и TK-634
P["stages"] = [s for s in P["stages"] if s["id"] != "TK-636"]
i = next(k for k, s in enumerate(P["stages"]) if s["id"] == "TK-634")   # в группе «Сделано 20.11» — первой (новее)
P["stages"].insert(i, STAGE)
old_head = "20.11: закрыты TK-634 —"
new_head = "20.11: закрыты TK-636 — монета как признак (оборот, возраст, волатильность, связь с рынком, соседи по кластеру): «не ясно»; TK-634 —"
if "TK-636" not in P["headline"]:
    assert old_head in P["headline"]
    P["headline"] = P["headline"].replace(old_head, new_head, 1)
nx = P["where"]["next"]["items"][0]
nx["t"] = "Признак «успешные пробои монеты» и признаки самой монеты (TK-625, TK-633, TK-634, TK-636): не ясно."
nx["s"] = ("Таблицы в $ и шарпе — в блоке под таблицами ниже: итог TK-625, раунд 2 (TK-633), импульс с потоком сделок (TK-634) и монета как признак с лесенкой суточного оборота (TK-636). "
           "Другая форма признака — только новой записью координатора.")
json.dump(P, open(prog, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
print("ok: строк", sum(len(t["rows"]) for sec in ROUND["sections"] for t in sec["tables"]), "p мин", num(pmin, 4), "Δ>0 в лесенке:", len(dpos))
