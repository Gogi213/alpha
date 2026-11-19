"""П-42 TK-624: сверка блока «Где мы» (вкладка «Ход работ») и вида «Базы» (вкладка «Эквити») страницы :8090 с источниками.

Использование: python tk624_check.py <index.html> <каталог с dom_where.json, dom_bases.json, report624.json (из tk624_shots.js)>
Что сверяется: (1) вшитые bases/progress == файлы data/; (2) итоги и помесячные суммы/сделки рядов == принятые отчёты (p42-maker-entry-2026-11-19.md, помесячная таблица);
(3) независимый пересчёт сумм, сделок, шарпа, макс. просадки по суточным рядам == строки bases.json == текст в DOM (таблица «Где мы», таблицы вида «Базы» трёх сторон, «Шорт + лонг» = S_base + L_base);
(4) концы кривых == итоги; подсказки при наведении == пересчёт; (5) тексты блока и этапов в DOM == progress.json, порядок групп; (6) состояния/хеши/переполнение из report624.json;
(7) в новом тексте нет «бп». Код возврата 1 при любом расхождении."""
from __future__ import annotations

import json
import math
import os
import re
import sys

import numpy as np

html_path, dom_dir = sys.argv[1:3]
html = open(html_path, encoding="utf-8").read()
data_dir = os.path.join(os.path.dirname(os.path.abspath(html_path)), "data")
n_ok, bad = 0, []


def eq(what, a, b, tol=0.0):
    global n_ok
    if isinstance(a, float) or isinstance(b, float):
        ok = a is not None and b is not None and abs(a - b) <= max(tol, 1e-9)
    else:
        ok = a == b
    if ok:
        n_ok += 1
    else:
        bad.append((what, a, b))


def emb(i):
    return json.loads(re.search(r'<script id="%s" type="application/json">(.*?)</script>' % i, html, re.S).group(1).replace("<\\/", "</"))


def jl(name):
    return json.load(open(os.path.join(dom_dir, name), encoding="utf-8"))


B, P = emb("bases-data"), emb("progress-data")
eq("bases вшитый == файл", json.dumps(B, sort_keys=True), json.dumps(json.load(open(os.path.join(data_dir, "bases.json"), encoding="utf-8")), sort_keys=True))
eq("progress вшитый == файл", json.dumps(P, sort_keys=True), json.dumps(json.load(open(os.path.join(data_dir, "progress.json"), encoding="utf-8")), sort_keys=True))
Wd, Bd, R = jl("dom_where.json"), jl("dom_bases.json"), jl("report624.json")


# ── форматирование как в странице: num(v, d, sign), ints
def num(v, d=3, sign=False):
    s = f"{abs(v):.{d}f}".replace(".", ",")
    z = float(s.replace(",", ".")) == 0
    return ("−" if v < 0 and not z else ("+" if sign and not z else "")) + s


money = lambda v, d=2: num(v, d, True)
ints = lambda n: re.sub(r"\B(?=(\d{3})+(?!\d))", " ", str(n))   # как в странице: неразрывный пробел


def stats(S, N):
    S, N = np.asarray(S, float), np.asarray(N, float)
    n, sd = len(S), float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    cum = np.cumsum(S)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return {"sum": float(S.sum()), "trades": int(N.sum()), "mean": float(S.sum() / N.sum()) if N.sum() else 0.0, "sharpe": float(S.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "maxdd": float((peak - cum).max()), "pos": int((S > 0).sum()), "tdays": int((N > 0).sum()), "days": n}


ML = B["month_len"]
ST = [0]
for n in ML:
    ST.append(ST[-1] + n)
SER = {k: (np.array(B["cells"][k]["daily_sum"]), np.array(B["cells"][k]["daily_n"])) for k in B["cells"]}
SER["SL"] = (SER["S_base"][0] + SER["L_base"][0], SER["S_base"][1] + SER["L_base"][1])
MON = {k: [stats(S[ST[m]:ST[m + 1]], N[ST[m]:ST[m + 1]]) for m in range(len(ML))] for k, (S, N) in SER.items()}
TOT = {k: stats(S, N) for k, (S, N) in SER.items()}

# ── 1. принятые отчёты: итоги и помесячная таблица (p42-maker-entry-2026-11-19.md)
ACC_TOT = {"L_base": (7.06, 621), "S_base": (259.14, 431), "S_mk1": (248.05, 372)}
ACC_M = {"L_base": [(-128.59, 130), (124.86, 131), (6.80, 152), (-95.47, 143), (99.46, 65)],
         "S_base": [(71.11, 96), (77.46, 108), (-22.88, 92), (110.61, 95), (22.85, 40)],
         "S_mk1": [(65.26, 81), (66.87, 93), (-4.30, 78), (85.79, 83), (34.43, 37)]}
eq("окно: 136 суток", (B["days"], sum(ML), len(B["month_names"])), (136, 136, 5))
for k in ACC_TOT:
    eq(f"{k}: итог $ == принятый", round(TOT[k]["sum"], 2), ACC_TOT[k][0], 0.0051)
    eq(f"{k}: сделок == принятому", TOT[k]["trades"], ACC_TOT[k][1])
    for m, (s_, n_) in enumerate(ACC_M[k]):
        eq(f"{k}: {B['month_names'][m]} $ == принятый", round(MON[k][m]["sum"], 2), s_, 0.0051)
        eq(f"{k}: {B['month_names'][m]} сделок == принятому", MON[k][m]["trades"], n_)
# ── 2. строки bases.json (tk624_table.py) == независимый пересчёт
for k in ACC_TOT:
    rows = B["cells"][k]["rows"]
    eq(f"{k}: строк bases.json", len(rows), 6)
    for i, st in enumerate(MON[k] + [TOT[k]]):
        r = rows[i]
        for f in ("sum", "trades", "mean", "sharpe", "maxdd"):
            eq(f"bases.json {k} строка {i} {f}", float(r[f]), float(st[f]), 1e-6)
        eq(f"bases.json {k} строка {i} дней в плюсе", (r["days_pos"], r["days_with_trades"]), (st["pos"], st["tdays"]))

# ── 3. DOM: таблица «Где мы»
W = P["where"]
eq("DOM где: строки", [x["id"] for x in Wd["lines"]], [x["id"] for x in W["rows"]])
for w, d in zip(W["rows"], Wd["lines"]):
    k, t, ms = w["id"], TOT[w["id"]], MON[w["id"]]
    eq(f"DOM где {k}: имя", d["name"], w["name"])
    exp = {"sum": money(t["sum"]), "trades": ints(t["trades"]), "mean": money(t["mean"], 3), "sharpe": num(t["sharpe"], 2, True), "maxdd": num(t["maxdd"], 2)}
    exp.update({f"m{i}": money(ms[i]["sum"]) for i in range(5)})
    eq(f"DOM где {k}: ячейки", d["cells"], exp)
eq("DOM где: заголовок", Wd["title"], W["title"])
eq("DOM где: вступление", Wd["lead"], W["lead"])
eq("DOM где: заголовок таблицы", Wd["table_title"], W["table_title"])
eq("DOM где: примечание таблицы", Wd["table_note"], W["table_note"])
for b in Wd["boxes"]:
    X = W[b["k"]]
    eq(f"DOM где {b['k']}: заголовок", b["title"], X["title"])
    eq(f"DOM где {b['k']}: пункты", b["items"], [i if isinstance(i, str) else i["t"] + " " + i["s"] for i in X["items"]])
eq("DOM где: три блока", sorted(b["k"] for b in Wd["boxes"]), ["broken", "candidate", "next"])
eq("DOM где: ссылка на «Базы»", Wd["link"], "#equity/short/bases")
eq("DOM где: легенда", Wd["legend"], [w["name"] for w in W["rows"]])
eq("DOM где: кривые", [(p["chart"], p["stroke"]) for p in Wd["paths"]], [(f"wh.{w['id']}", c) for w, c in zip(W["rows"], ["#2a78d6", "#1baf7a", "#eb6834"])])
for p in Wd["paths"]:
    eq(f"DOM где {p['chart']}: конец кривой == итог", p["final"], TOT[p["chart"].split(".")[1]]["sum"], 1e-6)


# ── подсказки: 3 точки наведения == пересчёт
def tip_expect(keys, i):
    d0 = np.datetime64(B["d0"]) + i
    s = d0.astype(str)
    out = [f"{s[8:10]}.{s[5:7]}"]
    for k in keys:
        S, N = SER[k]
        names = {"L_base": "Лонг · вход по рынку (L_base)", "S_base": "Шорт · вход по рынку (S_base)", "S_mk1": "Шорт · вход лимиткой (S_mk1)", "SL": "Шорт + лонг (S_base + L_base)"}
        out += [names[k], "Прибыль суток · сделок", f"{money(S[i])} $ · {ints(int(N[i]))}", "Накоплено", f"{money(float(np.cumsum(S)[i]))} $"]
    return " | ".join(out)


def tip_check(what, keys, hovers):
    for h in hovers:
        eq(f"{what}: подсказка показана", h["shown"], True)
        m = re.match(r"(\d\d)\.(\d\d)", h["text"])
        ok = m is not None
        eq(f"{what}: подсказка начинается с даты", ok, True)
        if ok:
            i = int((np.datetime64(f"2026-{m.group(2)}-{m.group(1)}") - np.datetime64(B["d0"])).astype(int))
            eq(f"{what}: подсказка сутки {i}", h["text"], tip_expect(keys, i))


tip_check("где", [w["id"] for w in W["rows"]], R["where"]["hover"])

# ── 4. DOM: вид «Базы»
LINES = {"short": ["S_base", "S_mk1"], "long": ["L_base"], "both": ["L_base", "S_base", "SL"]}
NAMES = {"L_base": "Лонг · вход по рынку (L_base)", "S_base": "Шорт · вход по рынку (S_base)", "S_mk1": "Шорт · вход лимиткой (S_mk1)", "SL": "Шорт + лонг (S_base + L_base)"}
COL = {"L_base": "#eb6834", "S_base": "#2a78d6", "S_mk1": "#1baf7a", "SL": "#52525b"}
for side, keys in LINES.items():
    d = Bd[side]
    eq(f"базы {side}: таблиц", [t["line"] for t in d["dom"]["tables"]], keys)
    for t in d["dom"]["tables"]:
        k = t["line"]
        eq(f"базы {side} {k}: строк", len(t["rows"]), 6)
        for r in t["rows"]:
            st = TOT[k] if r["row"] == "all" else MON[k][int(r["row"])]
            nm = f"всего · {B['days']} суток" if r["row"] == "all" else B["month_names"][int(r["row"])]
            eq(f"базы {side} {k} {r['row']}: название", r["name"], nm)
            eq(f"базы {side} {k} {r['row']}: ячейки", r["cells"], {"sum": money(st["sum"]), "trades": ints(st["trades"]), "mean": money(st["mean"], 3), "sharpe": num(st["sharpe"], 2, True),
                                                                   "maxdd": num(st["maxdd"], 2), "pos": f"{st['pos']} из {st['tdays']}"})
    eq(f"базы {side}: кривые", [(p["chart"], p["stroke"]) for p in d["dom"]["paths"]], [(f"bs.{k}", COL[k]) for k in keys])
    for p in d["dom"]["paths"]:
        eq(f"базы {side} {p['chart']}: конец кривой == итог", p["final"], TOT[p["chart"].split(".")[1]]["sum"], 1e-6)
    eq(f"базы {side}: легенда", d["dom"]["legend"], [NAMES[k] for k in keys])
    tip_check(f"базы {side}", keys, d["hover"])
    eq(f"базы {side}: состояние", (d["state"]["hash"], d["state"]["view"], d["state"]["side"], d["state"]["selected"]), (f"#equity/{side}/bases", "bases", side, ["equity"]))
    eq(f"базы {side}: подпись «по дню входа»", "по дню входа" in d["sub"], True)
    eq(f"базы {side}: переключатель компаундинга скрыт", d["cmpHidden"], True)
    eq(f"базы {side}: переполнение страницы", d["overflowX"] <= 0, True)
    eq(f"базы {side}: переполнение таблиц", d["dom"]["tableOverflow"] <= 0, True)
    eq(f"базы {side}: в примечании итоги баз", all(f"{k} {money(TOT[k]['sum'])} $" in d["dom"]["note"] for k in ACC_TOT), True)
    eq(f"базы {side}: заголовок карточки", d["dom"]["head"].startswith("Базы за 136 суток: 01.07 — 13.11"), True)
eq("базы: описание «short»", "Синяя — вход по рынку" in Bd["short"]["desc"], True)

# ── 5. DOM: этапы и группы == progress.json
seq, last = [], None
for s in P["stages"]:
    g = s.get("group")
    if g != last:
        seq.append({"g": g})
        last = g
    if not seq or "ids" not in seq[-1]:
        seq.append({"ids": []})
    seq[-1]["ids"].append(s["id"])
eq("DOM группы и этапы по порядку", Wd["groups"], seq)
STAT = {"done": "готово", "running": "идёт", "waiting": "ждёт"}
eq("DOM этапов", len(Wd["stages"]), len(P["stages"]))
for s, d in zip(P["stages"], Wd["stages"]):
    w = f"DOM этап {s['id']}"
    eq(w + " id", d["id"], s["id"])
    eq(w + " статус", (d["status"], d["badge"]), (s["status"], STAT[s["status"]]))
    eq(w + " шаги", ",".join(d["steps"]), ",".join(t["s"] for t in s.get("steps", [])))
    eq(w + " цифры", "|".join(d["numbers"]), "|".join(s["numbers"]))
    eq(w + " дальше", d["next"], s["next"])
    eq(w + " вопрос", d["answers"], s["answers"])
eq("DOM шапка хода работ", Wd["headline"], P["headline"])
eq("этапы: ровно один идёт (TK-621)", [s["id"] for s in P["stages"] if s["status"] == "running"], ["TK-621"])
eq("этапы: сегодняшние TK-615…623 на месте", [s["id"] for s in P["stages"] if s.get("group") == "Сделано сегодня, 19.11"], ["TK-599", "TK-615", "TK-616", "TK-617", "TK-618", "TK-619", "TK-620", "TK-622", "TK-623"])
eq("этапы: точки вперёд", [t["t"].split(" ")[0] for t in next(s for s in P["stages"] if s["id"] == "30.11–31.03")["steps"]], ["30.11", "31.12", "31.01", "28.02", "31.03.2027"])

# ── 6. состояния, хеши, ошибки, переполнение
eq("ошибок консоли и сети нет", R["errors"], [])
eq("вкладка по умолчанию — «Прогресс»", R["default"]["selected"], ["live"])
eq("«Ход работ»: состояние", (R["where"]["state"]["hash"], R["where"]["state"]["visible"]), ("#progress", ["pane-progress"]))
eq("«Ход работ»: нет горизонтальной прокрутки страницы", R["where"]["overflowX"] <= 0, True)
eq("«Где мы»: таблица не прокручивается", Wd["tableOverflow"] <= 0, True)
eq("ссылка из «Где мы» ведёт в «Базы»", (R["where"]["link"]["hash"], R["where"]["link"]["view"], R["where"]["link"]["side"]), ("#equity/short/bases", "bases", "short"))
eq("вид «Кривая» цел", (R["bases"]["backCurve"]["view"], R["bases"]["backCurve"]["cards"], R["bases"]["backCurve"]["cmpHidden"], "по времени закрытия" in R["bases"]["backCurve"]["sub"]), ("curve", ["ja", "fit"], False, True))
eq("вид «Форма» цел", (R["bases"]["backShape"]["view"], R["bases"]["backShape"]["cards"]), ("shape", ["ja", "fit"]))
eq("возврат в «Базы»", R["bases"]["toBases"]["view"], "bases")
H = R["hash"]
eq("хеш #equity/both/bases", (H["#equity/both/bases"]["view"], H["#equity/both/bases"]["side"], H["#equity/both/bases"]["bases"]), ("bases", "both", "both"))
eq("хеш #equity/long/bases", (H["#equity/long/bases"]["view"], H["#equity/long/bases"]["side"], H["#equity/long/bases"]["bases"]), ("bases", "long", "long"))
eq("хеш #equity/short — прежняя кривая", (H["#equity/short"]["view"], H["#equity/short"]["bases"]), ("curve", None))
eq("хеш #progress", (H["#progress"]["selected"], H["#progress"]["where"]), (["progress"], True))

# ── 7. «бп» и базисные пункты в новом тексте
txt = json.dumps([P["where"], [s for s in P["stages"] if s.get("group") != "Раньше (18.11 и раньше)"], Wd, Bd], ensure_ascii=False)
hit = re.findall(r"(?<![А-Яа-яA-Za-z])(?:бп|bp)(?![А-Яа-яA-Za-z])|базисн", txt)
eq("в новом тексте нет «бп»", hit, [])

print(f"проверок {n_ok + len(bad)}, расхождений {len(bad)}")
for w, a, b in bad[:40]:
    print("  РАСХОЖДЕНИЕ:", w, "| страница:", str(a)[:300], "| ожидалось:", str(b)[:300])
sys.exit(1 if bad else 0)
