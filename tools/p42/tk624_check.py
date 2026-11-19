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
eq("этапы: идущих нет, TK-633, TK-626 и TK-625 закрыты (группа «Сделано 20.11»)", ([s["id"] for s in P["stages"] if s["status"] == "running"], [(s["id"], s["status"]) for s in P["stages"] if s.get("group") == "Сделано 20.11"]), ([], [("TK-633", "done"), ("TK-626", "done"), ("TK-625", "done")]))
eq("этапы: сегодняшние TK-615…623 на месте", [s["id"] for s in P["stages"] if s.get("group") == "Сделано сегодня, 19.11"], ["TK-599", "TK-615", "TK-616", "TK-617", "TK-618", "TK-619", "TK-620", "TK-621", "TK-622", "TK-623"])
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
       [(x["fold"], x["title"], x["note"], [(t["title"], t["cols"], [(r["bold"], r["cells"]) for r in t["rows"]]) for t in x["tables"]]) for x in Wrs],
       [(bool(x.get("fold")), x["title"], x.get("note"), [(t["title"], t["cols"], [(i in t.get("bold", []), r) for i, r in enumerate(t["rows"])]) for t in x["tables"]]) for x in R0["sections"]])
    eq("DOM раунд 2: таблица единиц целиком видна, без горизонтальной прокрутки (колонки с переносом)", [t["overflow"] <= 0 for x in Wrs for t in x["tables"]], [True])
    eq("DOM раунд 2: блок раньше итога TK-625 (data-fbox=current ниже), «Признаки» не затронуты", [x["k"] for x in Wrs], ["0.0"])
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
