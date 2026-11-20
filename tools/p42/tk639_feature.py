#!/usr/bin/env python3
"""TK-639: блок «Число сделок за 6 и 12 ч и средний размер сделки» (Эквити → Где мы, «Признак: успешные пробои монеты», под раундом TK-638) + этап TK-639 в «Ход работ» из итогов юнита tk639-pnl.
Использование: python tk639_feature.py <каталог res TK-639 (cells39-*.json, counts39-*.json)> <ml_progress.json>
Источник: res/cells39-<вс>-<сторона>.json юнита tk639-pnl (отчёт docs/findings/p42-tradecount-size-2026-11-20.md, ветка TK-639); копия — docs/findings/p42-tradecount-size-2026-11-20/res/.
Лестницы: число сделок монеты за 6 ч и за 12 ч (≥ N) и средний размер сделки (оборот / число сделок) за 1 ч, 6 ч, 24 ч (≥ X и ≤ X) на четырёх общих квантилях, лонг и шорт; h775 — решающая, o566 — справочно, свёрнуто.
Пишет feature.rounds (раунды TK-633 … TK-638 остаются, раунд TK-639 — под ними), этап TK-639, строку «Что дальше» и заголовок в ml_progress.json (идемпотентно). Без бп, без медиан."""
import json
import os
import sys

res_dir, prog = sys.argv[1:3]
MONTHS = ["Июль, $", "Август, $", "Сентябрь, $", "Октябрь, $", "Ноябрь (1–13), $"]
SIDE = {"L": "Лонг", "S": "Шорт"}
FAM_TXT = {"NC360": "сделок за 6 ч", "NC720": "сделок за 12 ч", "SZ60": "размер сделки за 1 ч", "SZ360": "размер сделки за 6 ч", "SZ1440": "размер сделки за 24 ч"}
GROUPS = (("Число сделок монеты за 6 ч и за 12 ч", ("NC360", "NC720")), ("Средний размер сделки за 1 ч", ("SZ60",)), ("Средний размер сделки за 6 ч", ("SZ360",)), ("Средний размер сделки за 24 ч", ("SZ1440",)))
R = {(u, s): json.load(open(os.path.join(res_dir, "cells39-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
CN = {(u, s): json.load(open(os.path.join(res_dir, "counts39-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}


def num(v, d=2, sign=False):
    if v is None or v != v:
        return "—"
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


def label(c):
    sign = "≤" if c["dir"] == "le" else "≥"
    n = ("{:,}".format(int(c["N"])).replace(",", " ")) if c["fam"].startswith("NC") or c["N"] >= 100 else ("%g" % c["N"]).replace(".", ",")
    unit = "" if c["fam"].startswith("NC") else " $"
    return "%s %s %s%s (q%s)" % (FAM_TXT[c["fam"]], sign, n, unit, "/".join("%d" % round(p * 100) for p in c["ps"]))


def tables(u):
    out = []
    for s in "LS":
        b = R[(u, s)]["base"]
        for title, fams in GROUPS:
            cols = ["Ступень (абсолютный порог на все монеты)", "Сделок", "Сумма, $", "На сделку, $", "Шарп (год)"] + MONTHS + ["R:R", "Стоп, % маржи"]

            def row(name, c):
                st = c["rr"]["stops"]
                return [name, str(c["n"]) + (" (мало)" if c["few"] else ""), num(c["sum"], 1, True), num(c["mean"], 2, True), num(c["sharpe"], 2, True)] + [num(m[1], 0, True) for m in c["months"]] + [num(c["rr"]["rr"]), num(-st["mean_loss_pct"], 1) if st["n"] else "нет стопов"]
            rows = [row("вся база (без фильтра)", b)] + [row(label(c), R[(u, s)]["ladders"][c["cid"]]) for c in CN[(u, s)]["ladders"] if c["fam"] in fams]
            out.append({"title": f"{SIDE[s]} · {title} · база {num(b['sum'], 2, True)} $ на {b['n']} сделках, шарп {num(b['sharpe'], 2, True)}", "cols": cols, "rows": rows, "bold": [0], "wrap": [0]})
    return out


bh = {s: R[("h775", s)]["base"] for s in "LS"}
TEXT, NOTES, STAGE_NUMBERS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk639_page_text.json"), encoding="utf-8")).values()

ROUND = {
    "title": "Число сделок за 6 и 12 ч и средний размер сделки (TK-639, 20.11): описательно",
    "status": "done",
    "text": TEXT,
    "notes": NOTES,
    "sections": [
        {"title": "Честная вселенная: 775 контрактов (TK-621) — решающая: число сделок за 6 и 12 ч, средний размер сделки за 1, 6 и 24 ч", "note": "Один абсолютный порог на все монеты (общий квантиль 20 / 40 / 60 / 80 % сигналов базы h775 по зачётным диапазонам, 2 значащие цифры); сигнал, не прошедший порог, не торгуется; размер сделки — оборот / число сделок окна, $ на запись; «≥» оставляет крупные, «≤» — мелкие. Первая строка — вся база.", "tables": tables("h775")},
        {"title": "Прежние 566 монет — для сравнения (те же пороги в абсолютных числах)", "fold": True, "tables": tables("o566")},
    ],
}

STAGE = {
    "id": "TK-639", "title": "Число сделок за 6 и 12 ч и средний размер сделки (оборот / число сделок за 1, 6, 24 ч)", "status": "done", "group": "Сделано 20.11",
    "answers": "Как меняются деньги, если торговать только при числе сделок монеты за 6 или 12 ч не ниже порога и при среднем размере сделки (оборот в окне / число сделок в окне за 1, 6 и 24 ч) выше или ниже порога — отдельно лонг и шорт.",
    "numbers": STAGE_NUMBERS,
    "next": "Продолжение — только записью координатора и условиями критика.",
}

P = json.load(open(prog, encoding="utf-8"))
rounds = [r for r in P["feature"].get("rounds", []) if "TK-639" not in r["title"]]
assert len(rounds) == 4 and rounds[3]["title"].startswith("Титровка оборота и числа сделок (TK-638,"), "ожидались раунды TK-633, TK-634, TK-636, TK-638"
P["feature"]["rounds"] = rounds + [ROUND]
P["stages"] = [s for s in P["stages"] if s["id"] != "TK-639"]
i = next(k for k, s in enumerate(P["stages"]) if s["id"] == "TK-638")
P["stages"].insert(i, STAGE)
old_head = "20.11: закрыты TK-638 —"
new_head = "20.11: закрыты TK-639 — число сделок за 6 и 12 ч и средний размер сделки (описательные таблицы, без вердикта); TK-638 —"
if "TK-639" not in P["headline"]:
    assert old_head in P["headline"]
    P["headline"] = P["headline"].replace(old_head, new_head, 1)
nx = P["where"]["next"]["items"][0]
add = " Число сделок за 6 и 12 ч и средний размер сделки (TK-639) — лестницы в блоке ниже под титровкой оборота (TK-638); проверка вперёд — только записью координатора."
if "TK-639" not in nx["s"]:
    nx["s"] = nx["s"] + add
json.dump(P, open(prog, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
print("ok: таблиц", sum(len(sec["tables"]) for sec in ROUND["sections"]), "строк", sum(len(t["rows"]) for sec in ROUND["sections"] for t in sec["tables"]))
