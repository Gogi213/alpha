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
T625 = sys.argv[3] if len(sys.argv) > 3 else None   # каталог с tk625-step1-h775.json / tk625-step1-o566.json (итоговые таблицы TK-625, только чтение)
T634 = sys.argv[5] if len(sys.argv) > 5 else None   # TK-634: select34.json (итоги юнита tk634-pnl; рядом отчёт p42-impulse-flow-on-history-2026-11-20.md — каталог выше)
T636 = sys.argv[6] if len(sys.argv) > 6 else None   # TK-636: select36.json (итоги юнита tk636-pnl; рядом counts36-h775-<L|S>.json и отчёт p42-coin-as-feature-2026-11-20.md)
T633 = sys.argv[4] if len(sys.argv) > 4 else None   # TK-633: select33.json (итоги юнита tk633-pnl; рядом отчёт p42-breakout-history-r2-2026-11-20.md — каталог выше)
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
ACC_TOT = {"L_base": (7.06, 621), "S_base": (259.14, 431), "S_mk1": (248.05, 372),
           # TK-645: 775 монет — базы U2 (p42-honest-universe-2026-11-19.md), L_n24 (p42-turnover-grid-2026-11-20.md), NC360_q20 (p42-tradecount-size-2026-11-20.md)
           "L_b775": (193.17, 428), "S_b775": (223.14, 319), "L_n24": (294.31, 181), "S_nc360": (258.91, 252)}
ACC_M = {"L_base": [(-128.59, 130), (124.86, 131), (6.80, 152), (-95.47, 143), (99.46, 65)],
         "S_base": [(71.11, 96), (77.46, 108), (-22.88, 92), (110.61, 95), (22.85, 40)],
         "S_mk1": [(65.26, 81), (66.87, 93), (-4.30, 78), (85.79, 83), (34.43, 37)]}
ACC_M0 = {"L_n24": [88, 74, 50, 76, 6], "S_nc360": [75, 62, 25, 64, 33]}   # TK-645: помесячно в отчётах — целые $
eq("окно: 136 суток", (B["days"], sum(ML), len(B["month_names"])), (136, 136, 5))
for k in ACC_TOT:
    eq(f"{k}: итог $ == принятый", round(TOT[k]["sum"], 2), ACC_TOT[k][0], 0.0051)
    eq(f"{k}: сделок == принятому", TOT[k]["trades"], ACC_TOT[k][1])
    for m, (s_, n_) in enumerate(ACC_M.get(k, [])):
        eq(f"{k}: {B['month_names'][m]} $ == принятый", round(MON[k][m]["sum"], 2), s_, 0.0051)
        eq(f"{k}: {B['month_names'][m]} сделок == принятому", MON[k][m]["trades"], n_)
for k, ms in ACC_M0.items():
    for m, s_ in enumerate(ms):
        eq(f"{k}: {B['month_names'][m]} $ == принятый (целые)", round(MON[k][m]["sum"]), s_)
# ── 2. строки bases.json (tk624_table.py) == независимый пересчёт
for k in ACC_TOT:
    rows = B["cells"][k]["rows"]
    eq(f"{k}: строк bases.json", len(rows), 6)
    for i, st in enumerate(MON[k] + [TOT[k]]):
        r = rows[i]
        for f in ("sum", "trades", "mean", "sharpe", "maxdd"):
            eq(f"bases.json {k} строка {i} {f}", float(r[f]), float(st[f]), 1e-6)
        eq(f"bases.json {k} строка {i} дней в плюсе", (r["days_pos"], r["days_with_trades"]), (st["pos"], st["tdays"]))

LINES = {"short": ["S_base", "S_mk1", "S_b775", "S_nc360"], "long": ["L_base", "L_b775", "L_n24"], "both": ["L_base", "S_base", "SL"]}
NAMES = {"L_base": "Лонг · вход по рынку (L_base, 566 монет)", "S_base": "Шорт · вход по рынку (S_base, 566 монет)", "S_mk1": "Шорт · вход лимиткой (S_mk1, 566 монет)", "SL": "Шорт + лонг (S_base + L_base)",
         "L_b775": "Лонг-база, 775 монет (L_base)", "S_b775": "Шорт-база, 775 монет (S_base)",
         "L_n24": "Лонг · сделок за 24 ч ≥ 220 000 (L_n24, 775 монет)", "S_nc360": "Шорт · сделок за 6 ч ≥ 26 000 (NC360_q20, 775 монет)"}
COL = {"L_base": "#eb6834", "S_base": "#2a78d6", "S_mk1": "#1baf7a", "SL": "#52525b", "L_b775": "#eb6834", "S_b775": "#2a78d6", "L_n24": "#a3369c", "S_nc360": "#0e7490"}

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
eq("DOM где: кривые", [(p["chart"], p["stroke"]) for p in Wd["paths"]], [(f"wh.{w['id']}", COL[w["id"]]) for w in W["rows"]])
for p in Wd["paths"]:
    eq(f"DOM где {p['chart']}: конец кривой == итог", p["final"], TOT[p["chart"].split(".")[1]]["sum"], 1e-6)


# ── подсказки: 3 точки наведения == пересчёт
def tip_expect(keys, i):
    d0 = np.datetime64(B["d0"]) + i
    s = d0.astype(str)
    out = [f"{s[8:10]}.{s[5:7]}"]
    for k in keys:
        S, N = SER[k]
        out += [NAMES[k], "Прибыль суток · сделок", f"{money(S[i])} $ · {ints(int(N[i]))}", "Накоплено", f"{money(float(np.cumsum(S)[i]))} $"]
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
eq("DOM группы и этапы по порядку", Wd["progress"]["groups"], seq)
STAT = {"done": "готово", "running": "идёт", "waiting": "ждёт"}
eq("DOM этапов", len(Wd["progress"]["stages"]), len(P["stages"]))
for s, d in zip(P["stages"], Wd["progress"]["stages"]):
    w = f"DOM этап {s['id']}"
    eq(w + " id", d["id"], s["id"])
    eq(w + " статус", (d["status"], d["badge"]), (s["status"], STAT[s["status"]]))
    eq(w + " шаги", ",".join(d["steps"]), ",".join(t["s"] for t in s.get("steps", [])))
    eq(w + " цифры", "|".join(d["numbers"]), "|".join(s["numbers"]))
    eq(w + " дальше", d["next"], s["next"])
    eq(w + " вопрос", d["answers"], s["answers"])
eq("DOM шапка хода работ", Wd["progress"]["headline"], P["headline"])
eq("этапы (TK-645): идут TK-641…644; TK-637…625 закрыты (группа «Сделано 20.11»)", ([s["id"] for s in P["stages"] if s["status"] == "running"], [(s["id"], s["status"]) for s in P["stages"] if s.get("group") == "Сделано 20.11"]), (["TK-641", "TK-642", "TK-643", "TK-644"], [("TK-637", "done"), ("TK-639", "done"), ("TK-638", "done"), ("TK-636", "done"), ("TK-634", "done"), ("TK-633", "done"), ("TK-626", "done"), ("TK-625", "done")]))
eq("этапы: TK-615…623 на месте (группа «Сделано 19.11», TK-645)", [s["id"] for s in P["stages"] if s.get("group") == "Сделано 19.11"], ["TK-599", "TK-615", "TK-616", "TK-617", "TK-618", "TK-619", "TK-620", "TK-621", "TK-622", "TK-623"])
eq("этапы: точки вперёд", [t["t"].split(" ")[0] for t in next(s for s in P["stages"] if s["id"] == "30.11–31.03")["steps"]], ["30.11", "31.12", "31.01", "28.02", "31.03.2027"])

# ── 6. состояния, хеши, ошибки, переполнение
eq("ошибок консоли и сети нет", R["errors"], [])
eq("вкладка по умолчанию — «Прогресс»", R["default"]["selected"], ["live"])
eq("«Ход работ»: состояние", (R["where"]["progressState"]["hash"], R["where"]["progressState"]["visible"]), ("#progress", ["pane-progress"]))
eq("«Ход работ»: нет горизонтальной прокрутки страницы", R["where"]["progressOverflowX"] <= 0, True)
eq("«Ход работ»: нет денег — ни блока «Где мы», ни таблиц, ни кривых", R["where"]["progressMoney"], {"where": False, "tables": 0, "svg": 0})
eq("«Ход работ»: строка-ссылка на «Эквити»", (Wd["progress"]["link"], Wd["progress"]["linkText"]), ("#equity/short/where", "Деньги, шарп и кривые — вкладка «Эквити», вид «Где мы»"))
eq("«Эквити» · «Где мы»: состояние после ссылки", (R["where"]["link"]["hash"], R["where"]["link"]["view"], R["where"]["link"]["side"], R["where"]["link"]["visible"]), ("#equity/short/where", "where", "short", ["pane-equity"]))
eq("«Где мы»: переключатель сторон скрыт", R["where"]["tabsHidden"], True)
eq("«Где мы»: нет горизонтальной прокрутки страницы", R["where"]["overflowX"] <= 0, True)
eq("«Где мы»: таблицы не прокручиваются", Wd["tableOverflow"] <= 0, True)
# честная вселенная (TK-621): пересчёт по суточным рядам universe.json + сверка с принятым report.json TK-621 (если лежит рядом)
UNI = emb("universe-data")
eq("universe вшитый == файл", json.dumps(UNI, sort_keys=True), json.dumps(json.load(open(os.path.join(data_dir, "universe.json"), encoding="utf-8")), sort_keys=True))
RJ = "/data/p42-tk621/report.json"
RJ = json.load(open(RJ, encoding="utf-8")) if os.path.exists(RJ) else None
eq("DOM вселенные: заголовок и примечание", (Wd["universe"]["title"], Wd["universe"]["note"]), (W["universe"]["title"], W["universe"]["note"]))
eq("DOM вселенные: таблицы", [t["side"] for t in Wd["universe"]["tables"]], ["short", "long"])
MK = ["jul", "aug", "sep", "oct", "nov"]
for t in Wd["universe"]["tables"]:
    eq(f"DOM вселенные {t['side']}: порядок", [r["id"] for r in t["rows"]], [u["id"] for u in UNI["universes"]])
    for r, u in zip(t["rows"], UNI["universes"]):
        sd = UNI["sides"][t["side"]][u["id"]]
        st = stats(sd["daily_sum"], sd["daily_n"])
        ms = [stats(sd["daily_sum"][ST[m]:ST[m + 1]], sd["daily_n"][ST[m]:ST[m + 1]]) for m in range(5)]
        exp = {"sum": money(st["sum"]), "trades": ints(st["trades"]), "mean": money(st["mean"], 3), "sharpe": num(st["sharpe"], 2, True), "maxdd": num(st["maxdd"], 2)}
        exp.update({f"m{i}": money(ms[i]["sum"]) for i in range(5)})
        eq(f"DOM вселенные {t['side']} {u['id']}: ячейки", r["cells"], exp)
        eq(f"DOM вселенные {t['side']} {u['id']}: название", r["name"], f"{u['id']} · {u['name']}")
        eq(f"universe.json {t['side']} {u['id']}: итоговая строка", (round(sd["rows"][5]["sum"], 6), sd["rows"][5]["trades"]), (round(st["sum"], 6), st["trades"]), 1e-6)
        if RJ:
            q = RJ[u["tag"]][t["side"]]
            eq(f"report.json TK-621 {t['side']} {u['id']}: итог", (round(st["sum"], 2), st["trades"]), (q["all"]["sum"], q["all"]["n"]), 0.0051)
            for m in range(5):
                eq(f"report.json TK-621 {t['side']} {u['id']} {MK[m]}", (round(ms[m]["sum"], 2), ms[m]["trades"]), (q[MK[m]]["sum"], q[MK[m]]["n"]), 0.0051)
Fd = P["feature"]
Wf, Cf = Wd["feature"], Fd["current"]
eq("DOM признак (TK-625): заголовок, плашка, текст", (Wf["title"], Wf["badge"], Wf["lead"], Wf["box"], Wf["text"], Wf["notes"]),
   (Fd["title"], "готово" if Cf["status"] == "done" else "считается", Fd["lead"], Cf["title"], Cf["text"], Cf.get("notes", [])))
eq("признак: статус — готово (TK-625 прислал таблицу)", Cf["status"], "done")
eq("признак: разделы и таблицы на странице == progress.json", [(x["fold"], x["title"], [(t["title"], t["cols"], [(r["bold"], r["cells"]) for r in t["rows"]]) for t in x["tables"]]) for x in Wf["sections"]],
   [(bool(x.get("fold")), x["title"], [(t["title"], t["cols"], [(i in t.get("bold", []), r) for i, r in enumerate(t["rows"])]) for t in x["tables"]]) for x in Cf["sections"]])
eq("признак: честная вселенная раскрыта, прежние 566 свёрнуты", [x["fold"] for x in Wf["sections"]], [False, True])
eq("признак: нет горизонтальной прокрутки таблиц (раскрыто)", (R["where"]["featureOverflowOpen"]["page"] <= 0, R["where"]["featureOverflowOpen"]["tables"] >= 0), (True, True))
if T625:
    # независимый пересчёт строк из step1-*.json TK-625 (ступень 1, m = 1 NATR): колонки по именам, окно × корзина, база; сумма корзин == база
    BK, WIN = ["0", "1", "2–3", "4+", "нет значения"], [(7, "ok10_d7"), (14, "ok10_d14"), (30, "ok10_d30")]
    for sec, fn_, (ns, nl) in zip(Cf["sections"], ("step1-h775.json", "step1-o566.json"), ((319, 428), (431, 621))):
        src = json.load(open(os.path.join(T625, "tk625-" + fn_), encoding="utf-8"))
        eq(f"TK-625 {fn_}: def2 / code_id", (src["def"], src["code_id"]), ("def2", "3267c9a4e181"))
        for t, side, nb in zip(sec["tables"], ("S", "L"), (ns, nl)):
            base = src[side]["base"]
            eq(f"TK-625 {fn_} {side}: база сделок", base["n"], nb)
            eq(f"TK-625 {fn_} {side}: заголовок", t["title"], f"{'Шорт' if side == 'S' else 'Лонг'} · база {money(base['sum'])} $ на {base['n']} сделках, шарп {money(base['sharpe'])}")
            eq(f"TK-625 {fn_} {side}: число строк", len(t["rows"]), 1 + 3 * 5)
            def cells(b):
                return [str(b["n"]), money(b["sum"]), money(b["mean"]) if b["n"] else "—", money(b["sharpe"])] + [money(m[1]) for m in b["months"]]
            eq(f"TK-625 {fn_} {side}: строка базы", t["rows"][0], ["вся база", "без фильтра"] + cells(base))
            k = 1
            for dd, key in WIN:
                cols = src[side]["cols"][key]
                eq(f"TK-625 {fn_} {side} D{dd}: корзины делят базу (сделки)", sum(cols[b]["n"] for b in BK), base["n"])
                eq(f"TK-625 {fn_} {side} D{dd}: корзины делят базу (Σ $)", round(sum(cols[b]["sum"] for b in BK), 4), round(base["sum"], 4), 1e-4)
                for mi in range(5):
                    eq(f"TK-625 {fn_} {side} D{dd}: месяц {mi}", round(sum(cols[b]["months"][mi][1] for b in BK), 4), round(base["months"][mi][1], 4), 1e-4)
                for b in BK:
                    eq(f"TK-625 {fn_} {side} D{dd} {b}", t["rows"][k], [f"{dd} сут.", b] + cells(cols[b]))
                    k += 1
            eq(f"TK-625 {fn_} {side}: итоги базы == принятые отчёты (TK-621 / TK-599)", (round(base["sum"], 2), base["n"]), {("h", "S"): (223.14, 319), ("h", "L"): (193.17, 428), ("o", "S"): (259.14, 431), ("o", "L"): (7.06, 621)}[(fn_[6], side)])
# ── TK-633: блок «История пробоев, раунд 2» (feature.rounds[0]) и этап TK-633
Rd = Fd.get("rounds", [])
Wr, Wrs = Wf["rounds"], Wf["rsections"]
eq("TK-633: раундов на странице == progress.json", len(Wr), len(Rd))
if Rd:
    R0 = Rd[0]
    eq("DOM раунд 2: заголовок, текст, пометки", (Wr[0]["title"], Wr[0]["text"], Wr[0]["notes"]), (R0["title"], R0["text"], R0.get("notes", [])))
    eq("DOM раунд 2: статус — готово", R0["status"], "done")
    eq("DOM раунд 2: раздел (заголовок, пояснение), таблица (заголовок, колонки, строки) == progress.json",
       [(x["fold"], x["title"], x["note"], [(t["title"], t["cols"], [(r["bold"], r["cells"]) for r in t["rows"]]) for t in x["tables"]]) for x in Wrs if x["k"].startswith("0.")],
       [(bool(x.get("fold")), x["title"], x.get("note"), [(t["title"], t["cols"], [(i in t.get("bold", []), r) for i, r in enumerate(t["rows"])]) for t in x["tables"]]) for x in R0["sections"]])
    eq("DOM раунд 2: таблица единиц целиком видна, без горизонтальной прокрутки (колонки с переносом)", [t["overflow"] <= 0 for x in Wrs if x["k"].startswith("0.") for t in x["tables"]], [True])
    eq("DOM раунды: блоки раньше итога TK-625 (data-fbox=current ниже), раунд TK-633 — над раундом TK-634, раунд TK-636 — под ним (три раздела), «Признаки» не затронуты", [x["k"] for x in Wrs], ["0.0", "1.0", "2.0", "2.1", "2.2", "3.0", "3.1", "3.2", "4.0", "4.1"])   # TK-645: + раунды TK-638 и TK-639
    if T633:
        # независимый пересчёт строк из select33.json (итоги юнита tk633-pnl, def2, code_id e8959378f147): единицы × диапазон по пробным клеткам h775
        sel = json.load(open(T633, encoding="utf-8"))
        eq("TK-633 select33: def2 / code_id", (sel["def"], sel["code_id"]), ("def2", "e8959378f147"))
        eq("TK-633: Холм совместный m = 21 (9 + 12), заморозки нет", (sel["holm"]["m_joint"], sel["holm"]["m"], sel["passed"], sel["repl"]), (21, 12, [], {}))
        eq("TK-633: g1 не прошла ни одна пробная клетка (h775)", [r.get("g1") for sd in "LS" for r in sel["rows"][sd].values() if r["trial"]], [False] * 12)
        eq("TK-633: исходы единиц", {k: v[0] for k, v in sel["outcomes"].items()}, {"ЛШ": "не работает", "ШЛ": "не ясно", "ЛЛ-обр": "не ясно"})
        def fm(v, d=2, sg=True):
            z = f"{abs(v):.{d}f}".replace(".", ",")
            return ("−" if v < 0 and float(z.replace(",", ".")) != 0 else ("+" if sg and float(z.replace(",", ".")) != 0 else "")) + z
        def span(vals, d=2, sg=True, sep="…"):
            a, b = fm(min(vals), d, sg), fm(max(vals), d, sg)
            return a if a == b else a + sep + b
        tab = R0["sections"][0]["tables"][0]["rows"]
        for row_, (sd, kd, nm) in zip(tab, (("L", "X", "Лонг × шорт-история"), ("S", "X", "Шорт × лонг-история"), ("L", "I", "Лонг при лонг-истории = 0"))):
            cids = [c for c, r in sel["rows"][sd].items() if r["trial"] and c[0] == kd]
            sl = [sel["slices_h775"][sd][c] for c in cids]
            bs = sel["slices_h775"][sd]["база"]
            eq(f"TK-633 {nm}: название и число клеток", row_[0], f"{nm} · {len(cids)} " + ("клетки" if 1 < len(cids) < 5 else "клеток"))
            eq(f"TK-633 {nm}: сделок / Σ $ / шарп по клеткам", row_[2:5], [span([x["n"] for x in sl], 0, False, "–"), span([x["sum"] for x in sl]), span([x["sharpe"] for x in sl])])
            eq(f"TK-633 {nm}: база (сделок, Σ $, шарп)", row_[5:8], [str(bs["n"]), fm(bs["sum"]), fm(bs["sharpe"])])
            eq(f"TK-633 {nm}: Δ к базе", row_[8], span([sel["rows"][sd][c]["d"] for c in cids]))
            # Σ клетки ниже Σ базы — текст блока опирается на это; Δ клетки == (Σ_клетки/n_клетки − Σ_базы/n_базы) с допуском на пересчёт по суткам
            for c, x in zip(cids, sl):
                eq(f"TK-633 {nm} {c}: Σ ниже базы", x["sum"] < bs["sum"], True)
        eq("TK-633: строки таблицы — три единицы, исходы", [r[1] for r in tab], ["не работает", "не ясно", "не ясно, кандидат не заморожен"])
        # сверка с принятым отчётом (таблица «Пробные клетки h775»): сделки, Σ $ (до целого), $/сделку, шарп по 12 клеткам в порядке ЛШ, ЛЛ-обр, ШЛ
        rep_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(T633))), "p42-breakout-history-r2-2026-11-20.md")
        if os.path.exists(rep_path):
            txt = open(rep_path, encoding="utf-8").read().split("## Пробные клетки h775")[1].split("Прошли ворота")[0]
            lines = [l for l in txt.split(chr(10)) if re.match(r"\| (ЛШ|ЛЛ-обр|ШЛ) ", l)]
            eq("отчёт TK-633: 12 строк клеток", len(lines), 12)
            order = [("L", c) for c, r in sel["rows"]["L"].items() if r["trial"] and c[0] == "X"] + [("L", c) for c, r in sel["rows"]["L"].items() if r["trial"] and c[0] == "I"] + [("S", c) for c, r in sel["rows"]["S"].items() if r["trial"]]
            for l, (sd, c) in zip(lines, order):
                f_ = [x.strip() for x in l.strip().strip("|").split("|")]
                x = sel["slices_h775"][sd][c]
                eq(f"отчёт TK-633 {sd}/{c}: сделок, Σ $, $/сделку, шарп", f_[1:5], [str(x["n"]), fm(round(x["sum"]), 0), fm(x["sum"] / x["n"]), fm(x["sharpe"], 2, False)])
                eq(f"отчёт TK-633 {sd}/{c}: p", f_[6], fm(sel["rows"][sd][c]["p"], 3, False))
    st33 = [x for x in P["stages"] if x["id"] == "TK-633"]
    eq("TK-633: этап «Ход работ» есть, готово, группа «Сделано 20.11»", [(x["status"], x["group"]) for x in st33], [("done", "Сделано 20.11")])
    eq("TK-633 в «Ход работ» на странице (DOM) — этап и статус", [(s_["status"], s_["badge"]) for s_ in Wd["progress"]["stages"] if s_["id"] == "TK-633"], [("done", "готово")])
# ── TK-634: блок «Импульс и поток сделок на истории пробоев» (feature.rounds[1], под раундом TK-633) и этап TK-634
eq("TK-634: три раунда — TK-633, TK-634, затем TK-636", [x["title"].startswith(w) for x, w in zip(Rd, ("История пробоев, раунд 2 (TK-633", "Импульс и поток сделок на истории пробоев (TK-634", "Монета как признак (TK-636"))] + [len(Rd)], [True, True, True, 5])   # TK-645: + раунды TK-638, TK-639
if len(Rd) > 1:
    R1 = Rd[1]
    eq("DOM TK-634: заголовок, текст, пометки", (Wr[1]["title"], Wr[1]["text"], Wr[1]["notes"]), (R1["title"], R1["text"], R1.get("notes", [])))
    eq("DOM TK-634: статус — готово", R1["status"], "done")
    eq("DOM TK-634: раздел (заголовок, пояснение), таблица (заголовок, колонки, строки) == progress.json",
       [(x["fold"], x["title"], x["note"], [(t["title"], t["cols"], [(r["bold"], r["cells"]) for r in t["rows"]]) for t in x["tables"]]) for x in Wrs if x["k"].startswith("1.")],
       [(bool(x.get("fold")), x["title"], x.get("note"), [(t["title"], t["cols"], [(i in t.get("bold", []), r) for i, r in enumerate(t["rows"])]) for t in x["tables"]]) for x in R1["sections"]])
    eq("DOM TK-634: таблица целиком видна, без горизонтальной прокрутки", [t["overflow"] <= 0 for x in Wrs if x["k"].startswith("1.") for t in x["tables"]], [True])
    if T634:
        # независимый пересчёт 8 строк из select34.json (итоги юнита tk634-pnl, def2, code_id f2c1421d7848): клетки h775 — сторона × семейство × история
        sel4 = json.load(open(T634, encoding="utf-8"))
        eq("TK-634 select34: def2 / code_id", (sel4["def"], sel4["code_id"]), ("def2", "f2c1421d7848"))
        eq("TK-634: Холм совместный m = 29 (21 + 8), заморозки нет", (sel4["holm"]["m_joint"], sel4["holm"]["m"], len(sel4["holm"]["ps_old"]), sel4["passed"], sel4["repl"]), (29, 8, 21, [], {}))
        eq("TK-634: g1 не прошла ни одна пробная клетка (h775)", [r.get("g1") for sd in "LS" for r in sel4["rows"][sd].values() if r["trial"]], [False] * 8)
        eq("TK-634: исходы единиц", {k: v[0] for k, v in sel4["outcomes"].items()}, {"лонг·импульс": "не работает", "лонг·поток": "не работает", "шорт·импульс": "не ясно", "шорт·поток": "не ясно"})
        def fm4(v, d=2, sg=True):
            z = f"{abs(v):.{d}f}".replace(".", ",")
            return ("−" if v < 0 and float(z.replace(",", ".")) != 0 else ("+" if sg and float(z.replace(",", ".")) != 0 else "")) + z
        tab4 = R1["sections"][0]["tables"][0]["rows"]
        eq("TK-634: 8 строк таблицы, исходы по единицам", [r[1] for r in tab4], ["не работает"] * 4 + ["не ясно"] * 4)
        order4 = [(sd, f"{fam}_{ctx}") for sd in "LS" for fam in ("IMP", "FLOW") for ctx in ("own", "cross")]
        for row_, (sd, c) in zip(tab4, order4):
            sl, bs, rr = sel4["slices_h775"][sd][c], sel4["slices_h775"][sd]["база"], sel4["rows"][sd][c]
            eq(f"TK-634 {sd}/{c}: название", row_[0], f"{'Лонг' if sd == 'L' else 'Шорт'} · {'импульс' if c[0] == 'I' else 'поток'} · {'своя' if c.endswith('own') else 'чужая'} история")
            eq(f"TK-634 {sd}/{c}: сделок / Σ $ / шарп", row_[2:5], [str(sl["n"]), fm4(sl["sum"]), fm4(sl["sharpe"])])
            eq(f"TK-634 {sd}/{c}: база (сделок, Σ $, шарп)", row_[5:8], [str(bs["n"]), fm4(bs["sum"]), fm4(bs["sharpe"])])
            eq(f"TK-634 {sd}/{c}: Δ к базе ± ошибка", row_[8], f"{fm4(rr['d'])} ± {fm4(rr['se'], 2, False)}")
            eq(f"TK-634 {sd}/{c}: Σ ниже базы, допущена, g1 нет", (sl["sum"] < bs["sum"], rr["admitted"], rr["g1"]), (True, True, False))
            eq(f"TK-634 {sd}/{c}: Δ == разность $ на сделку (допуск на пересчёт по суткам)", abs((sl["sum"] / sl["n"] - bs["sum"] / bs["n"]) - rr["d"]) < 0.06, True)
        eq("TK-634: лучший p и порог Холма", (fm4(min(sel4["holm"]["ps"]), 3, False), fm4(0.05 / 29, 4, False)), ("0,075", "0,0017"))
        eq("TK-634: текст раунда содержит лучший p и порог", ("0,075" in R1["text"], "0,0017" in R1["text"]), (True, True))
        # сверка с принятым отчётом (таблица «h775 — решающая — деньги по клеткам»): сделки, Σ $ (до целого), $/сделку, шарп по 8 пробным клеткам
        rep4 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(T634))), "p42-impulse-flow-on-history-2026-11-20.md")
        if os.path.exists(rep4):
            lines4 = [l for l in open(rep4, encoding="utf-8").read().split(chr(10)) if re.match(r"\| (лонг|шорт) · (импульс|поток) · (своя|чужая) история \| пробная \|", l)][:8]
            eq("отчёт TK-634: 8 строк пробных клеток (h775)", len(lines4), 8)
            for l, (sd, c) in zip(lines4, order4):
                f_ = [x.strip() for x in l.strip().strip("|").split("|")]
                x = sel4["slices_h775"][sd][c]
                eq(f"отчёт TK-634 {sd}/{c}: сделок, Σ $, $/сделку, шарп", f_[2:6], [str(x["n"]), "%+.0f" % x["sum"], "%+.2f" % (x["sum"] / x["n"]), "%.2f" % x["sharpe"]])
    st34 = [x for x in P["stages"] if x["id"] == "TK-634"]
    eq("TK-634: этап «Ход работ» есть, готово, группа «Сделано 20.11»", [(x["status"], x["group"]) for x in st34], [("done", "Сделано 20.11")])
    eq("TK-634 в «Ход работ» на странице (DOM) — этап и статус", [(s_["status"], s_["badge"]) for s_ in Wd["progress"]["stages"] if s_["id"] == "TK-634"], [("done", "готово")])
# ── TK-636: блок «Монета как признак» (feature.rounds[2], под раундом TK-634) и этап TK-636
if len(Rd) > 2:
    R2 = Rd[2]
    eq("DOM TK-636: заголовок, текст, пометки", (Wr[2]["title"], Wr[2]["text"], Wr[2]["notes"]), (R2["title"], R2["text"], R2.get("notes", [])))
    eq("DOM TK-636: статус — готово", R2["status"], "done")
    eq("DOM TK-636: разделы (заголовок, пояснение), таблицы (заголовок, колонки, строки) == progress.json",
       [(x["fold"], x["title"], x["note"], [(t["title"], t["cols"], [(r["bold"], r["cells"]) for r in t["rows"]]) for t in x["tables"]]) for x in Wrs if x["k"].startswith("2.")],
       [(bool(x.get("fold")), x["title"], x.get("note"), [(t["title"], t["cols"], [(i in t.get("bold", []), r) for i, r in enumerate(t["rows"])]) for t in x["tables"]]) for x in R2["sections"]])
    eq("DOM TK-636: таблицы целиком видны, без горизонтальной прокрутки", [t["overflow"] <= 0 for x in Wrs if x["k"].startswith("2.") for t in x["tables"]], [True] * 6)
    if T636:
        # независимый пересчёт 48 строк из select36.json (итоги юнита tk636-pnl, def2, code_id 519f3a173dc3): лесенка TOV (h775, o566) и остальные 11 клеток h775
        sel6 = json.load(open(T636, encoding="utf-8"))
        def f6(v, d=2, sg=True):
            t_ = f"{abs(v):.{d}f}".replace(".", ",")
            z_ = float(t_.replace(",", ".")) == 0
            return ("−" if v < 0 and not z_ else ("+" if sg and not z_ else "")) + t_
        eq("TK-636 select36: def2 / code_id", (sel6["def"], sel6["code_id"]), ("def2", "519f3a173dc3"))
        eq("TK-636: Холм совместный m = 61 (29 + 32), заморозки нет", (sel6["holm"]["m_joint"], sel6["holm"]["m"], len(sel6["holm"]["ps_old"]), sel6["passed"], sel6["repl"]), (61, 32, 29, [], {}))
        eq("TK-636: g1 не прошла ни одна пробная клетка (h775)", [r.get("g1") for sd in "LS" for r in sel6["rows"][sd].values() if r and r["trial"]], [False] * 32)
        eq("TK-636: исходы сторон", {k: v[0] for k, v in sel6["outcomes"].items()}, {"лонг": "не ясно", "шорт": "не ясно"})
        TOVc = ["TOV_16", "TOV_24", "TOV_36", "TOV_54", "TOV_80"]
        OTH = [c_ for c_, r_ in sel6["rows"]["L"].items() if r_ and r_["trial"] and c_ not in TOVc]
        eq("TK-636: 11 остальных клеток", len(OTH), 11)
        plan = [(0, "slices_h775", "rows", TOVc, True), (1, "slices_h775", "rows", OTH, False), (2, "slices_o566", "rows_o566", TOVc, False)]
        for si, sk, rk, cids_, g5 in plan:
            tabs_ = R2["sections"][si]["tables"]
            for ti, sd in enumerate("LS"):
                rows_ = tabs_[ti]["rows"]
                bs = sel6[sk][sd]["база"]
                eq(f"TK-636 {si}/{sd}: база — сделок, Σ $, на сделку, шарп, месяцы", rows_[0][1:10], [str(bs["n"]), f6(bs["sum"]), f6(bs["mean"]), f6(bs["sharpe"])] + [f6(m_[1]) for m_ in bs["months"]])
                eq(f"TK-636 {si}/{sd}: число строк", len(rows_), 1 + len(cids_))
                for row_, c_ in zip(rows_[1:], cids_):
                    sl, rr = sel6[sk][sd][c_], sel6[rk][sd][c_]
                    eq(f"TK-636 {si}/{sd}/{c_}: сделок, Σ $, на сделку, шарп, месяцы", row_[1:10], [str(sl["n"]), f6(sl["sum"]), f6(sl["mean"]), f6(sl["sharpe"])] + [f6(m_[1]) for m_ in sl["months"]])
                    eq(f"TK-636 {si}/{sd}/{c_}: Δ ± ошибка, p", row_[10:12], [f"{f6(rr['d'])} ± {f6(rr['se'], 2, False)}", f6(rr["p"], 4, False) if rr.get("p") is not None else "—"])
                    eq(f"TK-636 {si}/{sd}/{c_}: Δ == разность $ на сделку (допуск на пересчёт по суткам)", abs((sl["sum"] / sl["n"] - bs["sum"] / bs["n"]) - rr["d"]) < 0.06, True)
                    if si == 0:
                        eq(f"TK-636 {sd}/{c_}: Σ ниже базы, допущена, g1 нет", (sl["sum"] < bs["sum"], rr["admitted"], rr["g1"]), (True, True, False))
                if g5:
                    eq(f"TK-636 {sd}: плато по построению — только 24 и 36 млн $", [r_[12] for r_ in rows_[1:]], ["нет", "да", "да", "нет", "нет"])
        eq("TK-636: лучший p и порог Холма", (f6(min(sel6["holm"]["ps"]), 4, False), f6(0.05 / 61, 4, False)), ("0,0067", "0,0008"))
        eq("TK-636: текст раунда содержит лучший p и порог", ("0,0067" in R2["text"], "0,0008" in R2["text"]), (True, True))
    st36 = [x for x in P["stages"] if x["id"] == "TK-636"]
    eq("TK-636: этап «Ход работ» есть, готово, группа «Сделано 20.11»", [(x["status"], x["group"]) for x in st36], [("done", "Сделано 20.11")])
    eq("TK-636 в «Ход работ» на странице (DOM) — этап и статус", [(s_["status"], s_["badge"]) for s_ in Wd["progress"]["stages"] if s_["id"] == "TK-636"], [("done", "готово")])
FD = R["features"]
eq("«Признаки»: пояснение TK-604 на странице", all(n in FD["text"] for n in emb("features-data")["sets"][4]["numbers"]), True)
eq("«Признаки»: нет горизонтальной прокрутки", FD["overflow"] <= 0, True)
eq("вид «Кривая» цел", (R["bases"]["backCurve"]["view"], R["bases"]["backCurve"]["cards"], R["bases"]["backCurve"]["cmpHidden"], "по времени закрытия" in R["bases"]["backCurve"]["sub"]), ("curve", ["ja", "fit"], False, True))
eq("вид «Форма» цел", (R["bases"]["backShape"]["view"], R["bases"]["backShape"]["cards"]), ("shape", ["ja", "fit"]))
eq("возврат в «Базы»", R["bases"]["toBases"]["view"], "bases")
H = R["hash"]
eq("хеш #equity/both/bases", (H["#equity/both/bases"]["view"], H["#equity/both/bases"]["side"], H["#equity/both/bases"]["bases"]), ("bases", "both", "both"))
eq("хеш #equity/long/bases", (H["#equity/long/bases"]["view"], H["#equity/long/bases"]["side"], H["#equity/long/bases"]["bases"]), ("bases", "long", "long"))
eq("хеш #equity/short — прежняя кривая", (H["#equity/short"]["view"], H["#equity/short"]["bases"]), ("curve", None))
eq("хеш #progress", (H["#progress"]["selected"], H["#progress"]["where"]), (["progress"], False))
eq("хеш #equity/short/where", (H["#equity/short/where"]["view"], H["#equity/short/where"]["side"], H["#equity/short/where"]["where"] is False), ("where", "short", True))

# ── 7. «бп» и базисные пункты в новом тексте
txt = json.dumps([P["where"], P["feature"], [s for s in P["stages"] if s.get("group") != "Раньше (18.11 и раньше)"], Wd, Bd, FD["text"]], ensure_ascii=False)
hit = re.findall(r"(?<![А-Яа-яA-Za-z])(?:бп|bp)(?![А-Яа-яA-Za-z])|базисн", txt)
eq("в новом тексте нет «бп»", hit, [])

print(f"проверок {n_ok + len(bad)}, расхождений {len(bad)}")
for w, a, b in bad[:40]:
    print("  РАСХОЖДЕНИЕ:", w, "| страница:", str(a)[:300], "| ожидалось:", str(b)[:300])
sys.exit(1 if bad else 0)
