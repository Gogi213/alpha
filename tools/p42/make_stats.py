"""П-42 v2: обычная торговая статистика, таблица по монетам и полные списки аутсайдеров → docs/findings/p42-stats-and-outsiders-2026-11-14.md.

  P42_ROOT=<путь>/data/p42v2 python tools/p42/make_stats.py
Читает готовые результаты прогонов (`tick-lat/`, `stage1/`, `final/`, `top3.json`, `outsiders-{long,short}.csv`); счёта не делает.
"""
import csv
import datetime
import json
import os
import re
import sys

import numpy as np

ROOT = os.environ.get("P42_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "data", "p42v2"))
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "findings", "p42-stats-and-outsiders-2026-11-14.md")
SIDES = ("long", "short")
SIDE_RU = {"long": "Лонг", "short": "Шорт"}
NOTIONAL = 500.0
D0 = datetime.date(2026, 9, 15)
NB = " "
MAXDIFF = [0.0]


def fixname(s):
    """Имена с иероглифами в части CSV записаны через cp1251 (mojibake): возвращаем исходный UTF-8, где возможно."""
    try:
        return s.encode("cp1251").decode("utf-8")
    except Exception:
        return s


def f(x, d=2, sign=False):
    if x is None:
        return "—"
    s = ("{:,.%df}" % d).format(abs(x)).replace(",", NB).replace(".", ",")
    if x < 0 and round(abs(x), d) != 0:
        return "−" + s
    return ("+" + s) if sign and x > 0 else s


def pct(x, d=1):
    return "—" if x is None else f(100 * x, d)


def date(i):
    return (D0 + datetime.timedelta(days=int(i))).strftime("%d.%m")


def load(rel, side):
    d = os.path.join(ROOT, rel)
    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    n_tp = int(re.search(r"n_tp: (\d+)", meta["params"]).group(1))
    trades = []
    for x in csv.DictReader(open(os.path.join(d, side + "-trades.csv"), encoding="utf-8")):
        if x.get("exit_ms"):
            hold = (int(x["exit_ms"]) - int(x["entry_ms"])) / 60000.0
        else:
            hold = float(int(x["exit_min"]) - int(x["entry_min"]))
        trades.append(dict(sym=fixname(x["sym"]), day=int(x["entry_day"]), net=float(x["net"]), kind=x["kind"],
                           takes=int(x["takes"]), ntp=n_tp, hold=hold))
    daily = np.array([float(x["pnl"]) for x in csv.DictReader(open(os.path.join(d, side + "-daily.csv"), encoding="utf-8"))])
    return dict(trades=trades, daily=daily)


def half(v, h):
    a = h == "A"
    return [t for t in v["trades"] if (t["day"] < 30) == a], v["daily"][0:30] if a else v["daily"][30:60]


def klass(t):
    if t["kind"] == "stop":
        return "stop"
    if t["kind"] == "forced":
        return "forced"
    if t["takes"] >= t["ntp"]:
        return "take"
    return "be" if t["kind"] == "be" else "trail"


def compute(tr, daily, off=0):
    n, nd = len(tr), len(daily)
    r = dict(n=n, nd=nd, per_day=n / nd, total=float(daily.sum()), usd_day=float(daily.sum()) / nd)
    cum = np.cumsum(daily)
    peak = dd = since = longest = 0.0
    for v in cum:
        if v > peak:
            peak, since = v, 0
        else:
            since += 1
        dd = max(dd, peak - v)
        longest = max(longest, since)
    r.update(dd=float(dd), longest=int(longest), tail=int(since))
    r["worst"], r["worst_i"] = float(daily.min()), int(daily.argmin()) + off
    r["best"], r["best_i"] = float(daily.max()), int(daily.argmax()) + off
    if not n:
        return r
    net = np.array([t["net"] for t in tr])
    w, l = net[net > 0], net[net < 0]
    r.update(win=len(w) / n, avg_win=float(w.mean()) if len(w) else None, avg_loss=float(l.mean()) if len(l) else None,
             avg=float(net.mean()), bps=float(net.mean()) / NOTIONAL * 1e4,
             pf=float(w.sum() / -l.sum()) if len(l) else None, trade_sum=float(net.sum()))
    MAXDIFF[0] = max(MAXDIFF[0], abs(r["trade_sum"] - r["total"]))
    ks = [klass(t) for t in tr]
    for k in ("stop", "be", "trail", "take", "forced"):
        r["sh_" + k] = ks.count(k) / n
    hold = np.array([t["hold"] for t in tr])
    r["hold"], r["hold_med"] = float(hold.mean()), float(np.median(hold))
    return r


def cand(srcA, srcB, side):
    va, vb = load(srcA, side), (load(srcB, side) if srcB != srcA else None)
    vb = vb or va
    (ta, da), (tb, db) = half(va, "A"), half(vb, "B")
    return dict(A=compute(ta, da, 0), B=compute(tb, db, 30), T=compute(ta + tb, np.concatenate([da, db])), trades=ta + tb)


def candidates(side):
    top = json.load(open(os.path.join(ROOT, "top3.json"), encoding="utf-8"))[side]
    c = [("Основа (правила бота), тики, задержка 200 мс на вход и выход", "tick-lat/b0_lx200", "tick-lat/b0_lx200", "осн200"),
         ("Основа (правила бота), минутки", "stage1/b0", "stage1/b0", "осн1м"),
         ("Перекрёстный, тики, 200 мс", "tick-lat/cB_%s_lx200" % side, "tick-lat/cA_%s_lx200" % side, "кр200"),
         ("Перекрёстный, минутки", "final/cB_%s" % side, "final/cA_%s" % side, "кр1м")]
    for t in top:
        c.append(("Топ-3 `%s`, тики, 200 мс" % t["vid"], "tick-lat/%s_lx200" % t["vid"], "tick-lat/%s_lx200" % t["vid"], "топ"))
    return c


METRICS = [
    ("Сделок", lambda r: str(r["n"])),
    ("Сделок в сутки", lambda r: f(r["per_day"], 2)),
    ("Доля прибыльных, %", lambda r: pct(r.get("win"))),
    ("Средняя прибыльная, $", lambda r: f(r.get("avg_win"))),
    ("Средняя убыточная, $", lambda r: f(r.get("avg_loss"))),
    ("Средняя сделка, $", lambda r: f(r.get("avg"))),
    ("Средняя сделка, bps от $500", lambda r: f(r.get("bps"), 1)),
    ("Profit factor", lambda r: f(r.get("pf"))),
    ("Итого, $", lambda r: f(r["total"])),
    ("$ в сутки", lambda r: f(r["usd_day"])),
    ("Макс. просадка, $ (по суткам)", lambda r: f(r["dd"])),
    ("Дольше всего без нового максимума, сут. (хвост на конец)", lambda r: "%d (%d)" % (r["longest"], r["tail"])),
    ("Худшие сутки, $ (дата)", lambda r: "%s (%s)" % (f(r["worst"]), date(r["worst_i"]))),
    ("Лучшие сутки, $ (дата)", lambda r: "%s (%s)" % (f(r["best"]), date(r["best_i"]))),
    ("Выход по стопу, %", lambda r: pct(r.get("sh_stop"))),
    ("Выход по безубытку, %", lambda r: pct(r.get("sh_be"))),
    ("Выход по трейлу, %", lambda r: pct(r.get("sh_trail"))),
    ("Закрыто тейками (все тейки исполнены), %", lambda r: pct(r.get("sh_take"))),
    ("Среднее время в сделке, мин (медиана)", lambda r: "—" if "hold" not in r else "%s (%s)" % (f(r["hold"], 0), f(r["hold_med"], 1))),
]


def detail(lbl, c):
    L = ["#### %s" % lbl, "", "| метрика | половина A (15.09–14.10) | половина B (15.10–13.11) | всего 60 суток |", "|---|---|---|---|"]
    for name, fn in METRICS:
        L.append("| %s | %s | %s | %s |" % (name, fn(c["A"]), fn(c["B"]), fn(c["T"])))
    return L + [""]


def summary(rows):
    L = ["| кандидат | сделок A / B | доля прибыльных, % | средняя сделка, $ (bps) | PF | итого, $ | $/сут. A / B / всего | макс. просадка, $ | без нового максимума, сут. | стопов, % |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for lbl, c in rows:
        A, B, T = c["A"], c["B"], c["T"]
        if not T["n"]:
            L.append("| %s | 0 / 0 | — | — | — | 0,00 | 0,00 / 0,00 / 0,00 | 0,00 | 0 | — |" % lbl)
            continue
        L.append("| %s | %d / %d | %s | %s (%s) | %s | %s | %s / %s / %s | %s | %d | %s |" % (
            lbl, A["n"], B["n"], pct(T["win"]), f(T["avg"]), f(T["bps"], 1), f(T["pf"]), f(T["total"]),
            f(A["usd_day"]), f(B["usd_day"]), f(T["usd_day"]), f(T["dd"]), T["longest"], pct(T["sh_stop"])))
    return L


def per_coin(trades):
    by = {}
    for t in trades:
        a = by.setdefault(t["sym"], [0, 0, 0.0])
        a[0] += 1
        a[1] += 1 if t["net"] > 0 else 0
        a[2] += t["net"]
    rows = sorted(by.items(), key=lambda kv: (kv[1][2], kv[0]))
    L = ["| № | монета | сделок | доля прибыльных, % | итого, $ | $ на сделку |", "|---|---|---|---|---|---|"]
    for i, (s, (n, w, p)) in enumerate(rows, 1):
        L.append("| %d | %s | %d | %s | %s | %s |" % (i, s, n, f(100 * w / n, 0), f(p), f(p / n)))
    neg = sum(1 for _, (n, w, p) in rows if p < 0)
    return L, len(rows), neg, sum(p for _, (_, _, p) in rows)


def outsiders(side):
    rows = [x for x in csv.DictReader(open(os.path.join(ROOT, "outsiders-%s.csv" % side), encoding="utf-8"))]
    out = {}
    for eng in ("1m", "tick"):
        for cl in ("100", "90"):
            z = [x for x in rows if x["engine"] == eng and x["class"] == cl]
            z.sort(key=lambda x: float(x["net_sum_cells"]))
            out[(eng, cl)] = z
    return out


def out_table(z):
    L = ["| № | монета | клеток в минусе / всего | сделок (сумма по клеткам) | итого, $ (сумма по клеткам) | $ на сделку | в основе: сделок, $ |", "|---|---|---|---|---|---|---|"]
    for i, x in enumerate(z, 1):
        n, p = int(x["trades_sum_cells"]), float(x["net_sum_cells"])
        L.append("| %d | %s | %s / %s | %d | %s | %s | %s, %s |" % (i, fixname(x["sym"]), x["cells_neg"], x["cells"], n, f(p), f(p / n if n else None),
                                                                      x["base_trades"], f(float(x["base_net"]))))
    return L


def tot(z):
    return len(z), sum(int(x["trades_sum_cells"]) for x in z), sum(float(x["net_sum_cells"]) for x in z)


def main():
    data = {}
    for side in SIDES:
        data[side] = [(lbl, cand(a, b, side), tag) for lbl, a, b, tag in candidates(side)]
    # сверка с отчётом v2 ($/сут. половин A / B, сделок)
    chk = {("long", "осн200"): (-1.23, 0.95, 344, 375), ("short", "осн200"): (-0.52, -9.06, 305, 313),
           ("long", "осн1м"): (-10.67, -7.90, 86, 106), ("short", "осн1м"): (0.78, 1.27, 76, 70),
           ("short", "кр200"): (-0.61, 2.51, 137, 213), ("short", "кр1м"): (-1.94, 5.56, 47, 59)}
    for side in SIDES:
        for lbl, c, tag in data[side]:
            k = (side, tag)
            if k in chk:
                a, b, na, nb = chk[k]
                ok = abs(c["A"]["usd_day"] - a) < 0.015 and abs(c["B"]["usd_day"] - b) < 0.015 and c["A"]["n"] == na and c["B"]["n"] == nb
                print("check", side, tag, "OK" if ok else "MISMATCH", f(c["A"]["usd_day"]), f(c["B"]["usd_day"]), c["A"]["n"], c["B"]["n"], file=sys.stderr)
    W = []
    W += ["# П-42 (пробой часового уровня): обычная торговая статистика и полные списки аутсайдеров, лонг и шорт отдельно (14.11.2026)", ""]
    W += ["Слова владельца (14.11): «ты не сделал статистику нормальную и не сделал что я сказал по худшим»; раньше: «собери список абсолютных аутсайдеров. возможно будут монеты которые в минусе на всех клетках. отдельно лонг отдельно шорт»; «сделай вывод куда то».", ""]
    W += ["## Результат", ""]
    W += ["Основа = правила бота (витрина), $500 на сделку, 60 суток 15.09–13.11, 566 монет Binance USD-M:", "",
          "| сторона, движок | сделок | прибыльных, % | средняя сделка, $ (bps) | PF | итого, $ | $/сут. A / B / всего | макс. просадка, $ | без нового максимума, сут. (хвост) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for side in SIDES:
        for i, en in ((0, "тики, 200 мс"), (1, "минутки")):
            c = data[side][i][1]
            T = c["T"]
            W.append("| %s, %s | %d | %s | %s (%s) | %s | **%s** | %s / %s / %s | %s | %d (%d) |" % (
                SIDE_RU[side], en, T["n"], pct(T["win"]), f(T["avg"]), f(T["bps"], 1), f(T["pf"]), f(T["total"]),
                f(c["A"]["usd_day"]), f(c["B"]["usd_day"]), f(T["usd_day"]), f(T["dd"]), T["longest"], T["tail"]))
    W += ["", "Абсолютные аутсайдеры (монет; найдено на этих данных): в минусе во всех клетках / в ≥ 90 % клеток (100 % и 90–99 % вместе); полные списки — раздел 3.", "",
          "| сторона | минутки: 100 % | минутки: ≥ 90 % | тики: 100 % | тики: ≥ 90 % |", "|---|---|---|---|---|"]
    out_long, out_short = outsiders("long"), outsiders("short")
    for side, o in (("long", out_long), ("short", out_short)):
        n = lambda eng, cl: len(o[(eng, cl)])
        W.append("| %s | %d | %d | %d | %d |" % (SIDE_RU[side], n("1m", "100"), n("1m", "100") + n("1m", "90"), n("tick", "100"), n("tick", "100") + n("tick", "90")))
    W += [""]
    W += ["- **Персистентность и исключение:** аутсайдеры одной половины в другой в минусе не чаще обычной доли (лонг на тиках: 2 из 12 и 4 из 12 при обычной доле 31 % и 30 %); у шорта на тиках 13 из 21 при 35 % — p = 0,011, после поправки Бонферрони ×8 ≈ 0,09 — не значимо. Исключение аутсайдеров базу не чинит: вне выборки, где они выбраны, |Δ| ≤ $2,6/сут., база не становится положительной. **Список — не фильтр для бота без новых суток.**"]
    W += ["- **Вердикт по сторонам (как в отчёте v2):** лонг — на минутках опровергнуто, на тиках при 200 мс «не ясно»; шорт — на тиках опровергнуто через половину B, на минутках «не ясно». Подтверждённых кандидатов нет.", ""]
    W += ["## Как читать", ""]
    W += ["- Окно 15.09–13.11.2026 (60 суток), половины A = 15.09–14.10, B = 15.10–13.11; позиция $500, тариф владельца, проскальзывание 1 bp; универсум 566 бессрочных Binance USD-M. «Основа» — правила бота (витрина). «Перекрёстный» — значения параметров, подобранные на другой половине: в A — вариант, подобранный на B, в B — вариант, подобранный на A. «Топ-3» — три лучших по худшей половине кандидата шага B (отбор на тех же данных).",
          "- Тики: вход и выходы по сделкам Binance (`aggTrades`), основное чтение — задержка 200 мс на входы и выходы (слова владельца «ну давай задержка будет 0.2»). Минутки — шаг A: вход по открытию следующей минуты, стоп по low минутки (грубее: сделок в 3–4 раза меньше).",
          "- Сделка относится к половине по дню входа. «Итого», «$ в сутки», просадка, лучшие/худшие сутки и «без нового максимума» — по суточному ряду отчёта; сумма P&L сделок половины совпадает с суточной суммой (наибольшее расхождение по всем таблицам — $%s). Средние, доля прибыльных и PF — по сделкам (net после комиссий и фандинга)." % f(MAXDIFF[0], 2),
          "- Макс. просадка — от пика счёта (старт = 0) до дна по суточному ряду; внутрисуточная не измерима. «Без нового максимума» — KPI В-120: самый долгий период (сутки) от максимума до нового максимума счёта, в скобках — незакрытый хвост на конец окна (старт = максимум 0). Для половин ряд начинается с 0 заново.",
          "- Выходы (взаимоисключающие): «стоп» — жёсткий стоп до первого тейка; «безубыток» — остаток закрыт по безубытку после первого тейка; «трейл» — остаток закрыт по трейлу; «тейки» — исполнены все лимитки-тейки (остаток 0). Одна принудительная сделка на конце окна (если есть) в этих долях — остаток до 100 %.",
          "- Время в сделке: тики — от входа до выхода в мс; минутки — разность минут (±1 мин).", ""]
    W += ["## 1. Стандартная статистика по сторонам", ""]
    for side in SIDES:
        W += ["### %s" % SIDE_RU[side], "", "Итог по кандидатам (всё в $; подробно — ниже):", ""]
        W += summary([(lbl, c) for lbl, c, _ in data[side]]) + [""]
        for lbl, c, _ in data[side]:
            if c["T"]["n"] == 0:
                W += ["#### %s" % lbl, "", "Сделок нет ни в A, ни в B (кандидат не торгует).", ""]
                continue
            W += detail(lbl, c)
    W += ["## 2. По монетам: основа, тики, задержка 200 мс (сначала худшие)", "",
          "Все монеты со сделками; сумма по монетам = итого за 60 суток по сделкам.", ""]
    for side in SIDES:
        tr = data[side][0][1]["trades"]
        L, n, neg, s = per_coin(tr)
        W += ["### %s: %d монет со сделками, в минусе %d, сумма %s $" % (SIDE_RU[side], n, neg, f(s)), ""] + L + [""]
    W += ["## 3. Абсолютные аутсайдеры (найдено на этих данных)", ""]
    W += ["«Клетка» — вариант движка, в котором у монеты есть сделки: минутки — все варианты шага A (≈120 у стороны); тики — 26 клеток стороны (кандидаты, топ-3, задержка/проскальзывание; клетки с задержкой и проскальзыванием делят сделки, поэтому «100 %» на тиках мягче, чем выглядит). Класс определён при ≥ 5 клетках монеты. «100 %» — в минусе во всех своих клетках; «90–99 %» — в минусе в ≥ 90 %, но не в 100 %; «≥ 90 %» = оба списка вместе. Сделки и $ — суммы по клеткам (сделка в k клетках считается k раз). «В основе» — только базовый прогон (`b0`) движка. Иероглифические имена восстановлены из записи в CSV, где часть имён испорчена кодировкой.", ""]
    for side, o in (("long", out_long), ("short", out_short)):
        W += ["### %s" % SIDE_RU[side], ""]
        W += ["| список | монет | сделок | итого, $ |", "|---|---|---|---|"]
        for eng, en in (("1m", "минутки"), ("tick", "тики")):
            a, b = tot(o[(eng, "100")]), tot(o[(eng, "90")])
            W.append("| %s, 100 %% | %d | %d | %s |" % (en, *a[:2], f(a[2], 0)))
            W.append("| %s, 90–99 %% | %d | %d | %s |" % (en, *b[:2], f(b[2], 0)))
            W.append("| %s, ≥ 90 %% (вместе) | %d | %d | %s |" % (en, a[0] + b[0], a[1] + b[1], f(a[2] + b[2], 0)))
        W.append("")
        for eng, en in (("1m", "минутки"), ("tick", "тики")):
            for cl, cn in (("100", "в минусе в 100 % клеток"), ("90", "в минусе в 90–99 % клеток")):
                z = o[(eng, cl)]
                W += ["#### %s, %s, %s: %d монет" % (SIDE_RU[side], en, cn, len(z)), ""]
                W += out_table(z) if z else ["Таких монет нет.", ""]
                W.append("")
    W += ["### Персистентность и исключение — простыми словами", ""]
    W += ["Проверка: выбираем «100 %»-аутсайдеров по одной половине и смотрим те же монеты в другой (и наоборот). Если бы аутсайдеры были свойством монет, в другой половине они были бы в минусе заметно чаще обычной доли убыточных монет. Числа ниже — из отчёта v2, новых счётов нет.", "",
          "| сторона / движок | A→B: выбрано / со сделками в B / в минусе в B | B→A: выбрано / со сделками в A / в минусе в A | обычная доля убыточных монет (A / B) |", "|---|---|---|---|",
          "| Лонг, минутки | 16 / 16 / 7 | 14 / 12 / 4 | 50 % / 46 % |",
          "| Лонг, тики | 18 / 12 / 2 | 25 / 12 / 4 | 30 % / 31 % |",
          "| Шорт, минутки | 1 / 1 / 0 | 7 / 5 / 3 | 44 % / 41 % |",
          "| Шорт, тики | 23 / 8 / 5 | 27 / 13 / 8 | 40 % / 32 % |", "",
          "- **Лонг:** аутсайдеры одной половины в другой в минусе не чаще обычной доли (минутки 7 из 16 и 4 из 12; тики 2 из 12 и 4 из 12) — **не держатся**.",
          "- **Шорт на тиках:** 5 из 8 и 8 из 13 (62–63 % против 32–40 %). Односторонний биномиальный p: A→B 0,17; B→A 0,027; вместе 13 из 21 при 35 % — p = 0,011. Это одна из 8 проверок («100 %», стороны × движки × направления): поправка Бонферрони ×8 даёт ≈ 0,22 (B→A) и ≈ 0,09 (вместе) — **не значимо**; клетки с задержкой/проскальзыванием делят сделки, поэтому p оптимистичен. Шорт на минутках — 1 и 7 монет, сравнивать нечего.",
          "- **Исключение:** база без выбранных на одной половине монет, на другой половине — разница с полной базой |Δ| ≤ $2,6/сут.; в 15 из 16 проверок интервал накрывает 0 или упирается в 0 (единственное исключение — шорт, тики, A→B, 100 %: Δ +1,75 [+0,02; +3,74]); база ни в одной проверке не становится положительной. Там же, где монеты выбраны, эффект +$4…+$24/сут. на тиках — это эффект отбора, а не свойство монет.",
          "- **Итог: «найдено на этих данных».** Список абсолютных аутсайдеров — описание окна 15.09–13.11, не фильтр: на новых сутках он не проверялся и в другой половине не воспроизводится.", ""]
    W += ["## 4. Краткий вывод", ""]
    W += ["- **Что проверено.** Пробой часового уровня по правилам бота (витрина Викинг), лонг и шорт отдельно, 566 бессрочных Binance USD-M (оборот ≥ $12 млн), 60 суток 15.09–13.11, $500 на сделку. Шаг A — минутки; шаг B — вход и выходы по сделкам при задержке 200 мс; кандидаты — основа, перекрёстный, топ-3; повторный вход по уровню — без изменения вердиктов (отчёт v2). Эта страница — обычная статистика и списки худших монет по тем же прогонам; новых счётов нет.",
          "- **Лонг.** Минутки — опровергнуто устойчиво (основа −$10,67 / −$7,90 в сутки по половинам). Тики при 200 мс — около нуля, знак смешанный, интервалы накрывают 0 — «не ясно»; опровержение остаётся только при нулевой, нереализуемой задержке. Перекрёстный кандидат у лонга не торгует (0 сделок).",
          "- **Шорт.** Минутки — «не ясно» (основа +$0,78 / +$1,27 в сутки). Тики при 200 мс — основа в минусе в обеих половинах (−$0,52 / −$9,06 в сутки), опровергнута через половину B; перекрёстный и минутная база расходятся со знаком — «не ясно». Положительный знак минутной базы на тиках не воспроизводится.",
          "- **Аутсайдеры.** Списки составлены полностью (раздел 3); в другой половине они не держатся (лонг) либо держатся слабо и незначимо (шорт, тики: p ≈ 0,09 после поправки); исключение базу не чинит. Это находка на данных, а не правило отбора монет.",
          "- **Что не охвачено.** Новые сутки (всё — одно окно 15.09–13.11); исполнение внутри секунды (задержка 200 мс — модель, размер сделки и глубина не моделируются; проскальзывание 1 bp); монеты вне универсума ≥ $12 млн; другие биржи; плечо; демо-ожидание «после издержек», не прогноз. P&L «в сутки» — среднее по суткам окна, не прогноз дохода.", ""]
    W += ["## Артефакты", "",
          "- Генератор: `tools/p42/make_stats.py` на ветке `bb/42-stats-outsiders-v1-thr_ewewq28522` (читает `data/p42v2/` — каталог в `.gitignore`; на srv `/data/p42v2/out/`). Источники: `tick-lat/*_lx200`, `stage1/b0`, `final/cA_*`, `final/cB_*`, `top3.json`, `outsiders-{long,short}.csv`.",
          "- Основной отчёт v2: `docs/findings/p42-level-breakout-v2-2026-11-14.md` и протокол `docs/research/P-42-level-breakout.md` (§13) — на ветке `bb/42-v2-finish-v1-thr_7cy3ypv2kx`.", ""]
    txt = "\n".join(W)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8", newline="\n").write(txt)
    print("written", OUT, len(txt), "max |trade sum - daily| = %.2f" % MAXDIFF[0], file=sys.stderr)


if __name__ == "__main__":
    main()
