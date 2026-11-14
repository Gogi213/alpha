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

import persist

ROOT = os.environ.get("P42_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "data", "p42v2"))
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "findings", "p42-stats-and-outsiders-2026-11-14.md")
SIDES = ("long", "short")
LAT = 4  # основное чтение тиков (В-157)
TD = "tick-lx%d" % LAT
TG = "_lx%d" % LAT
TAGMAIN = "вход+выход %d" % LAT
OUTS = os.environ.get("P42_OUTS") or ROOT  # каталог аутсайдеров (по клеткам основного чтения)
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
    c = [("Основа (правила бота), тики, задержка 4 мс на вход и выход", "%s/b0%s" % (TD, TG), "%s/b0%s" % (TD, TG), "осн" + "tick"),
         ("Основа (правила бота), минутки", "stage1/b0", "stage1/b0", "осн1м"),
         ("Перекрёстный, тики, 4 мс", "%s/cB_%s%s" % (TD, side, TG), "%s/cA_%s%s" % (TD, side, TG), "кр" + "tick"),
         ("Перекрёстный, минутки", "final/cB_%s" % side, "final/cA_%s" % side, "кр1м")]
    for t in top:
        c.append(("Топ-3 `%s`, тики, 4 мс" % t["vid"], "%s/%s%s" % (TD, t["vid"], TG), "%s/%s%s" % (TD, t["vid"], TG), "топ"))
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
    rows = [x for x in csv.DictReader(open(os.path.join(OUTS, "outsiders-%s.csv" % side), encoding="utf-8"))]
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


def lat_json():
    return json.load(open(os.path.join(ROOT, "report-lat.json"), encoding="utf-8"))


def verdict_changes(LT, side, a="вход+выход %d" % LAT, b="вход+выход 200"):
    """Кандидаты, у которых вердикт при основном чтении (4 мс) отличается от 200 мс."""
    out = []
    for key, v in LT[side].items():
        nm, tg = key.split("|")
        if tg != a:
            continue
        o = LT[side].get("%s|%s" % (nm, b))
        if o and o["verdict"] != v["verdict"]:
            out.append("%s: %s → %s" % (nm, o["verdict"], v["verdict"]))
    return out


def pers_old():
    """Персистентность на клетках lat 0 (tickfull8), пересчитанная тем же кодом: P42_OUTS_OLD — каталог её outsiders-summary.json."""
    d = os.environ.get("P42_OUTS_OLD")
    p = os.path.join(d, "outsiders-summary.json") if d else None
    return persist.rows(p) if p and os.path.exists(p) else None


def fp(x):
    return f(x, 3 if x < 0.1 else 2)


def tops(LT, side):
    return "», «".join(v["verdict"] for k, v in LT[side].items() if k.startswith("топ-3") and k.endswith(TAGMAIN))


def excl_text(EX):
    # на тиках класс «90 %» совпадает со «100 %» (клеток не более 9): такие строки не считаем дважды
    dup = {(x["side"], x["selected_on"], x["n_coins"]) for x in EX if x["engine"] == "tick" and x["class"] == "100"}
    ev = [x for x in EX if not (x["engine"] == "tick" and x["class"] == "90" and (x["side"], x["selected_on"], x["n_coins"]) in dup)]
    mx = max(abs(x["eval_delta"]) for x in ev)
    lab = lambda x: "%s, %s, %s→%s, %s %%" % (SIDE_RU[x["side"]].lower(), "минутки" if x["engine"] == "1m" else "тики", x["selected_on"], x["evaluated_on"], x["class"])
    above = [x for x in ev if x["eval_delta_lo"] > 0]
    below = [x for x in ev if x["eval_delta_hi"] < 0]
    flip = [x for x in ev if x["eval_base_mean"] < 0 < x["eval_excl_mean"]]
    s = "вне выборки, где монеты выбраны, исключение даёт |Δ| ≤ $%s/сут.; интервал Δ целиком выше 0 — в %d из %d проверок%s, целиком ниже 0 — в %d" % (
        f(mx), len(above), len(ev),
        (" (" + "; ".join("%s: Δ %s [%s; %s]" % (lab(x), f(x["eval_delta"], 2, True), f(x["eval_delta_lo"], 2, True), f(x["eval_delta_hi"], 2, True)) for x in above) + ")") if above else "", len(below))
    if flip:
        s += "; отрицательная база после исключения становится положительной в %d из %d проверок (%s) — интервал Δ накрывает 0, то есть в пределах шума" % (
            len(flip), len(ev), "; ".join("%s: %s → %s" % (lab(x), f(x["eval_base_mean"], 2, True), f(x["eval_excl_mean"], 2, True)) for x in flip))
    else:
        s += "; база ни в одной проверке не становится положительной"
    tk = [x for x in ev if x["engine"] == "tick"]
    s += ". В той же половине, где выбор сделан, эффект на тиках %s…%s $/сут. — это эффект отбора, а не свойство монет" % (f(min(x["sel_delta"] for x in tk), 0, True), f(max(x["sel_delta"] for x in tk), 0, True))
    return s


def main():
    LT = lat_json()
    data = {}
    for side in SIDES:
        data[side] = [(lbl, cand(a, b, side), tag) for lbl, a, b, tag in candidates(side)]
    # сверка с report-lat.json (тики, основное чтение) и отчётом v2 (минутки): $/сут. половин A / B, сделок
    chk = {("long", "осн1м"): (-10.67, -7.90, 86, 106), ("short", "осн1м"): (0.78, 1.27, 76, 70), ("short", "кр1м"): (-1.94, 5.56, 47, 59)}
    for side in SIDES:
        for tag, nm in (("осн", "база"), ("кр", "перекрёстный")):
            h = LT[side]["%s|%s" % (nm, TAGMAIN)]["halves"]
            chk[(side, tag + "tick")] = (h["A"]["mean"], h["B"]["mean"], h["A"]["trades"], h["B"]["trades"])
    for side in SIDES:
        for lbl, c, tag in data[side]:
            k = (side, tag)
            if k in chk:
                a, b, na, nb = chk[k]
                ok = abs(c["A"]["usd_day"] - a) < 0.015 and abs(c["B"]["usd_day"] - b) < 0.015 and c["A"]["n"] == na and c["B"]["n"] == nb
                print("check", side, tag, "OK" if ok else "MISMATCH", f(c["A"]["usd_day"]), f(c["B"]["usd_day"]), c["A"]["n"], c["B"]["n"], file=sys.stderr)
    OS = json.load(open(os.path.join(OUTS, "outsiders-summary.json"), encoding="utf-8"))
    EX = json.load(open(os.path.join(OUTS, "outsiders-excl.json"), encoding="utf-8"))
    out_long, out_short = outsiders("long"), outsiders("short")
    pr = persist.rows(os.path.join(OUTS, "outsiders-summary.json"))
    po = pers_old()
    ncell = OS["long|tick"]["cells"]
    tl = {r["side"]: r for r in pr if r["engine"] == "tick"}
    pl, ps = tl["long"], tl["short"]
    old_s = next(r for r in po if r["side"] == "short" and r["engine"] == "tick") if po else None
    old_txt = ("На клетках lat 0 (прежний расчёт) у шорта на тиках было p = %s вместе (Бонферрони ≈ %s) — при 4 мс слабый признак персистентности исчез." % (fp(old_s["both"]["p"]), fp(old_s["both"]["p_bonf"]))) if old_s else ""
    lb, sb_ = LT["long"]["база|" + TAGMAIN], LT["short"]["база|" + TAGMAIN]
    chg = verdict_changes(LT, "long") + verdict_changes(LT, "short")
    chg_txt = "**Против 200 мс вердикты не изменились** (база, перекрёстный, топ-3, оба варианта повторного входа)." if not chg else "Изменились против 200 мс: " + "; ".join(chg) + "."
    W = []
    W += ["# П-42 (пробой часового уровня): обычная торговая статистика и полные списки аутсайдеров, лонг и шорт отдельно (14.11.2026)", ""]
    W += ["Слова владельца (14.11): «ты не сделал статистику нормальную и не сделал что я сказал по худшим»; раньше: «собери список абсолютных аутсайдеров. возможно будут монеты которые в минусе на всех клетках. отдельно лонг отдельно шорт»; «сделай вывод куда то». **Основное чтение тиков — задержка 4 мс на входы и выходы** (слова владельца, В-157: «я те кажется кривую задержку сказал. не 200 мс а 4 мс»; отклонение протокола §13.4 п. 8); 200 мс, 250, 1000 и lat 0 — справочно.", ""]
    W += ["## Результат", "", "Проценты (доходность и просадка в % от счёта $2 500, позиция $500) по месяцам и монетам — раздел 5 и CSV в `docs/findings/p42-per-coin-month/`.", ""]
    W += ["Основа = правила бота (витрина), $500 на сделку, 60 суток 15.09–13.11, 566 монет Binance USD-M:", "",
          "| сторона, движок | сделок | прибыльных, % | средняя сделка, $ (bps) | PF | итого, $ | $/сут. A / B / всего | макс. просадка, $ | без нового максимума, сут. (хвост) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for side in SIDES:
        for i, en in ((0, "тики, 4 мс"), (1, "минутки")):
            c = data[side][i][1]
            T = c["T"]
            W.append("| %s, %s | %d | %s | %s (%s) | %s | **%s** | %s / %s / %s | %s | %d (%d) |" % (
                SIDE_RU[side], en, T["n"], pct(T["win"]), f(T["avg"]), f(T["bps"], 1), f(T["pf"]), f(T["total"]),
                f(c["A"]["usd_day"]), f(c["B"]["usd_day"]), f(T["usd_day"]), f(T["dd"]), T["longest"], T["tail"]))
    W += ["", "Абсолютные аутсайдеры (монет; найдено на этих данных): в минусе во всех клетках / в ≥ 90 %% клеток (100 %% и 90–99 %% вместе); тики — %d клеток (варианты правил при задержке 4 мс), поэтому при ≥ 5 клетках «≥ 90 %%» совпадает со «100 %%»; полные списки — раздел 3." % ncell, "",
          "| сторона | минутки: 100 % | минутки: ≥ 90 % | тики: 100 % | тики: ≥ 90 % |", "|---|---|---|---|---|"]
    for side, o in (("long", out_long), ("short", out_short)):
        nn = lambda eng, cl: len(o[(eng, cl)])
        W.append("| %s | %d | %d | %d | %d |" % (SIDE_RU[side], nn("1m", "100"), nn("1m", "100") + nn("1m", "90"), nn("tick", "100"), nn("tick", "100") + nn("tick", "90")))
    W += [""]
    W += ["- **Персистентность (тики, 4 мс):** лонг — аутсайдеры одной половины в другой в минусе: %d из %d и %d из %d при обычной доле %s %% и %s %% (p = %s и %s; вместе %d из %d, p = %s) — **не держатся**; шорт — %d из %d и %d из %d при %s %% и %s %% (p = %s и %s; вместе %d из %d при %s %%: p = %s, после поправки Бонферрони ×8 ≈ %s) — **не значимо**. %s" % (
        pl["A->B"]["k"], pl["A->B"]["n"], pl["B->A"]["k"], pl["B->A"]["n"], f(100 * pl["A->B"]["p0"], 0), f(100 * pl["B->A"]["p0"], 0), fp(pl["A->B"]["p"]), fp(pl["B->A"]["p"]), pl["both"]["k"], pl["both"]["n"], fp(pl["both"]["p"]),
        ps["A->B"]["k"], ps["A->B"]["n"], ps["B->A"]["k"], ps["B->A"]["n"], f(100 * ps["A->B"]["p0"], 0), f(100 * ps["B->A"]["p0"], 0), fp(ps["A->B"]["p"]), fp(ps["B->A"]["p"]), ps["both"]["k"], ps["both"]["n"], f(100 * ps["both"]["p0"], 0), fp(ps["both"]["p"]), fp(ps["both"]["p_bonf"]), old_txt)]
    W += ["- **Исключение аутсайдеров базу не чинит:** %s. **Список — не фильтр для бота без новых суток.**" % excl_text(EX)]
    W += ["- **Вердикт по сторонам (§10, 4 мс; как в отчёте v2):** лонг — на минутках опровергнуто (устойчиво), на тиках база «%s» (A %s, B %s $/сут.), перекрёстный «%s» (0 сделок), топ-3 «%s»; шорт — на минутках «не ясно», на тиках база «%s» (A %s, B %s; в B без 2 худших суток %s, без 2 худших монет %s), перекрёстный «%s» (A %s, B %s), топ-3 «%s». Подтверждённых кандидатов нет. %s" % (
        lb["verdict"], f(lb["halves"]["A"]["mean"], 2, True), f(lb["halves"]["B"]["mean"], 2, True),
        LT["long"]["перекрёстный|" + TAGMAIN]["verdict"].split(" (")[0], tops(LT, "long"),
        sb_["verdict"], f(sb_["halves"]["A"]["mean"], 2, True), f(sb_["halves"]["B"]["mean"], 2, True),
        f(sb_["halves"]["B"]["no_worst2_days"]["mean"], 2, True), f(sb_["halves"]["B"]["no_worst2_coins"]["mean"], 2, True),
        LT["short"]["перекрёстный|" + TAGMAIN]["verdict"], f(LT["short"]["перекрёстный|" + TAGMAIN]["halves"]["A"]["mean"], 2, True), f(LT["short"]["перекрёстный|" + TAGMAIN]["halves"]["B"]["mean"], 2, True),
        tops(LT, "short"), chg_txt)]
    import excl22_doc
    W += excl22_doc.result_bullet()
    W += excl22_doc.wf_bullet()
    W += excl22_doc.retitr_bullet()
    W += ["", "## Как читать", ""]
    W += ["- Окно 15.09–13.11.2026 (60 суток), половины A = 15.09–14.10, B = 15.10–13.11; позиция $500, тариф владельца, проскальзывание 1 bp; универсум 566 бессрочных Binance USD-M. «Основа» — правила бота (витрина). «Перекрёстный» — значения параметров, подобранные на другой половине: в A — вариант, подобранный на B, в B — вариант, подобранный на A. «Топ-3» — три лучших по худшей половине кандидата шага B (отбор на тех же данных).",
          "- Тики: вход и выходы по сделкам Binance (`aggTrades`), основное чтение — задержка 4 мс на входы и выходы (В-157, «я те кажется кривую задержку сказал. не 200 мс а 4 мс»; до этого было 200 мс по слову «ну давай задержка будет 0.2»); 0 / 200 / 250 / 1000 мс — справочно. Минутки — шаг A: вход по открытию следующей минуты, стоп по low минутки (грубее: сделок в 3–4 раза меньше).",
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
    W += ["## 2. По монетам: основа, тики, задержка 4 мс (сначала худшие)", "",
          "Все монеты со сделками; сумма по монетам = итого за 60 суток по сделкам.", ""]
    for side in SIDES:
        tr = data[side][0][1]["trades"]
        L, n, neg, s = per_coin(tr)
        W += ["### %s: %d монет со сделками, в минусе %d, сумма %s $" % (SIDE_RU[side], n, neg, f(s)), ""] + L + [""]
    W += ["## 3. Абсолютные аутсайдеры (найдено на этих данных)", ""]
    W += ["«Клетка» — вариант движка, в котором у монеты есть сделки: минутки — все варианты шага A (≈120 у стороны); **тики — %d клеток: варианты правил при задержке 4 мс на вход и выход** (`*_lx4`: основа, перекрёстные `cA_short`/`cB_short`, топ-3 — `j_be_0__t_low_2`, `j_be_0__t_low_3`, `v_be_0`, `v_e_0p1`, `v_n_bounce_3`, `v_tf_240`; клетки делят сделки — они мало различаются, поэтому «100 %%» мягче, чем выглядит). Класс определён при ≥ 5 клетках монеты. «100 %%» — в минусе во всех своих клетках; «90–99 %%» — в минусе в ≥ 90 %%, но не в 100 %%; «≥ 90 %%» = оба списка вместе. **Тики: клеток всего %d, поэтому 8 из 9 клеток = 89 %% < 90 %% — класс «90–99 %%» на тиках пуст, «≥ 90 %%» = «100 %%».** Сделки и $ — суммы по клеткам (сделка в k клетках считается k раз). «В основе» — только базовый прогон движка (`b0`; тики — `b0_lx4`). Иероглифические имена восстановлены из записи в CSV, где часть имён испорчена кодировкой." % (ncell, ncell), "",
          "Прежний расчёт аутсайдеров на тиках шёл по клеткам lat 0 (`tick-tickfull8`, 26 клеток: lat 0/250/1000, проскальзывание); по замечанию итоговой проверки и слову владельца (В-157) пересчитан на клетках 4 мс. Минутки не менялись.", ""]
    for side, o in (("long", out_long), ("short", out_short)):
        W += ["### %s" % SIDE_RU[side], ""]
        W += ["| список | монет | сделок | итого, $ |", "|---|---|---|---|"]
        for eng, en in (("1m", "минутки"), ("tick", "тики, 4 мс")):
            a, b = tot(o[(eng, "100")]), tot(o[(eng, "90")])
            W.append("| %s, 100 %% | %d | %d | %s |" % (en, a[0], a[1], f(a[2], 0)))
            W.append("| %s, 90–99 %% | %d | %d | %s |" % (en, b[0], b[1], f(b[2], 0)))
            W.append("| %s, ≥ 90 %% (вместе) | %d | %d | %s |" % (en, a[0] + b[0], a[1] + b[1], f(a[2] + b[2], 0)))
        W.append("")
        for eng, en in (("1m", "минутки"), ("tick", "тики, 4 мс")):
            for cl, cn in (("100", "в минусе в 100 % клеток"), ("90", "в минусе в 90–99 % клеток")):
                z = o[(eng, cl)]
                W += ["#### %s, %s, %s: %d монет" % (SIDE_RU[side], en, cn, len(z)), ""]
                W += out_table(z) if z else ["Таких монет нет.", ""]
                W.append("")
    W += ["### Персистентность и исключение — простыми словами", ""]
    W += ["Проверка: выбираем «100 %»-аутсайдеров по одной половине и смотрим те же монеты в другой (и наоборот). Если бы аутсайдеры были свойством монет, в другой половине они были бы в минусе заметно чаще обычной доли убыточных монет. Тики — клетки 4 мс; минутки — как раньше. Односторонний биномиальный p пересчитан по `outsiders-summary.json` (`tools/p42/persist.py`); поправка Бонферрони ×8 (стороны × движки × направления), «вместе» — сумма двух направлений при средневзвешенной обычной доле.", "",
          "| сторона / движок | A→B: выбрано / со сделками в B / в минусе в B (обычная доля) | B→A: выбрано / со сделками в A / в минусе в A (обычная доля) | p: A→B / B→A / вместе | Бонферрони ×8 (вместе) |", "|---|---|---|---|---|"]
    for r in pr:
        ab, ba, bo = r["A->B"], r["B->A"], r["both"]
        W.append("| %s, %s | %d / %d / %d (%s %%) | %d / %d / %d (%s %%) | %s / %s / %s | %s |" % (
            SIDE_RU[r["side"]], "минутки" if r["engine"] == "1m" else "тики, 4 мс", ab["selected"], ab["n"], ab["k"], f(100 * ab["p0"], 0), ba["selected"], ba["n"], ba["k"], f(100 * ba["p0"], 0),
            fp(ab["p"]) if ab["p"] is not None else "—", fp(ba["p"]) if ba["p"] is not None else "—", fp(bo["p"]) if bo["p"] is not None else "—", fp(bo["p_bonf"]) if bo["p"] is not None else "—"))
    W += ["",
          "- **Лонг, тики:** %d из %d и %d из %d (обычная доля %s %% и %s %%) — **не держатся**; вместе %d из %d, p = %s." % (pl["A->B"]["k"], pl["A->B"]["n"], pl["B->A"]["k"], pl["B->A"]["n"], f(100 * pl["A->B"]["p0"], 0), f(100 * pl["B->A"]["p0"], 0), pl["both"]["k"], pl["both"]["n"], fp(pl["both"]["p"])),
          "- **Шорт, тики:** %d из %d и %d из %d (%s %% и %s %%); вместе %d из %d при %s %%: p = %s, после Бонферрони ×8 ≈ %s — **не значимо**; клетки делят сделки, поэтому p оптимистичен. Шорт на минутках — 1 и 7 монет, сравнивать нечего. %s" % (
              ps["A->B"]["k"], ps["A->B"]["n"], ps["B->A"]["k"], ps["B->A"]["n"], f(100 * ps["A->B"]["p0"], 0), f(100 * ps["B->A"]["p0"], 0), ps["both"]["k"], ps["both"]["n"], f(100 * ps["both"]["p0"], 0), fp(ps["both"]["p"]), fp(ps["both"]["p_bonf"]), old_txt),
          "- **Исключение** (база без выбранных на одной половине монет, на другой половине; минутки — точный пересчёт, тики — приближённо: вычитание P&L монет, слоты не пополняются): %s." % excl_text(EX),
          "- **Итог: «найдено на этих данных».** Список абсолютных аутсайдеров — описание окна 15.09–13.11, не фильтр: на новых сутках он не проверялся и в другой половине не воспроизводится.", ""]
    W += ["## 4. Краткий вывод", ""]
    W += ["- **Что проверено.** Пробой часового уровня по правилам бота (витрина Викинг), лонг и шорт отдельно, 566 бессрочных Binance USD-M (оборот ≥ $12 млн), 60 суток 15.09–13.11, $500 на сделку. Шаг A — минутки; шаг B — вход и выходы по сделкам при задержке 4 мс (основное чтение, В-157); кандидаты — основа, перекрёстный, топ-3; повторный вход по уровню — без изменения вердиктов (отчёт v2). Эта страница — обычная статистика и списки худших монет по тем же прогонам; новых испытаний нет.",
          "- **Лонг.** Минутки — опровергнуто устойчиво (основа −$10,67 / −$7,90 в сутки по половинам). Тики при 4 мс — около нуля: основа %s / %s $/сут. (A / B), знак смешанный, интервалы накрывают 0 — «%s»; перекрёстный кандидат не торгует (0 сделок); топ-3 — «%s»." % (
              f(lb["halves"]["A"]["mean"], 2, True), f(lb["halves"]["B"]["mean"], 2, True), lb["verdict"], tops(LT, "long")),
          "- **Шорт.** Минутки — «не ясно» (основа +$0,78 / +$1,27 в сутки). Тики при 4 мс — основа %s / %s $/сут. (A / B): плюс в A, минус в B; «%s» через половину B (в B знак «−» держится без 2 худших суток %s и монет %s, интервал накрывает 0); перекрёстный — «%s»; топ-3 — «%s». Положительный знак минутной базы на тиках не воспроизводится." % (
              f(sb_["halves"]["A"]["mean"], 2, True), f(sb_["halves"]["B"]["mean"], 2, True), sb_["verdict"], f(sb_["halves"]["B"]["no_worst2_days"]["mean"], 2, True), f(sb_["halves"]["B"]["no_worst2_coins"]["mean"], 2, True),
              LT["short"]["перекрёстный|" + TAGMAIN]["verdict"], tops(LT, "short")),
          "- **Аутсайдеры.** Списки составлены полностью (раздел 3); в другой половине они не держатся (тики при 4 мс: p вместе %s у лонга и %s у шорта, после Бонферрони %s у шорта); исключение базу не чинит. Это находка на данных, а не правило отбора монет." % (fp(pl["both"]["p"]), fp(ps["both"]["p"]), fp(ps["both"]["p_bonf"])),
          "- **Что не охвачено.** Новые сутки (всё — одно окно 15.09–13.11); исполнение внутри секунды (задержка 4 мс — модель; размер сделки и глубина не моделируются; проскальзывание 1 bp); монеты вне универсума ≥ $12 млн; другие биржи; плечо; демо-ожидание «после издержек», не прогноз. P&L «в сутки» — среднее по суткам окна, не прогноз дохода.", ""]
    import coin_month
    W += coin_month.section()
    W += excl22_doc.section_excl22()
    W += excl22_doc.section_walkfwd()
    W += excl22_doc.section_retitr()
    W += ["## Артефакты", "",
          "- Генератор: `tools/p42/make_stats.py` на ветке `bb/42-stats-outsiders-v1-thr_ewewq28522` (читает `data/p42v2/` — каталог в `.gitignore`; на srv `/data/p42v2/out/`). Источники: `tick-lx4/*_lx4` (основное чтение, 4 мс), `stage1/b0`, `final/cA_*`, `final/cB_*`, `top3.json`, `report-lat.json`; аутсайдеры на тиках — `outsiders-*.csv`, `outsiders-summary.json`, `outsiders-excl.json` по клеткам `*_lx4` (`tools/p42/outsiders.py` с `P42_TICK=tick-lx4 P42_TICK_RE='^(b0|cA_|cB_|j_|v_).*lx4$' P42_TICK_BASE=b0_lx4`).",
          "- По монетам × месяцам и в процентах: `tools/p42/coin_month.py` → `docs/findings/p42-per-coin-month/{long,short,portfolio}.csv` (раздел 5).",
          "- Основной отчёт v2: `docs/findings/p42-level-breakout-v2-2026-11-14.md` и протокол `docs/research/P-42-level-breakout.md` (§13) — на ветке `bb/42-v2-finish-v1-thr_7cy3ypv2kx`; правки основного чтения — на ветке `bb/42-stats-outsiders-v1-thr_ewewq28522`.", ""]
    W += ["- Без 22 монет (§13.4 п. 9, раздел 6): `tools/p42/runexcl22.sh` (точный пересчёт на srv: `--symbols` = 544, минутки и тики 4 мс, контроль `b0` на 566) → `tools/p42/excl22.py` → `data/p42v2/report-excl22.json`; разделы 6–7 и лист Excel «Без 22 монет» — `tools/p42/excl22_doc.py`. Чёрный список walk-forward (§13.7, раздел 7): `tools/p42/walkfwd.py` → `data/p42v2-wf/` (отчёт, списки по суткам). Перетитровка без 22 монет (§13.8, раздел 8): `tools/p42/run544.sh` (шаг A и шаг B на srv, `--symbols` = 544) → `tools/p42/tickvars.py`, `tick544vars.py` → `tools/p42/retitr544.py`, `tools/p42/count_pos.py` → `data/p42v2-544/` (`report-544.json`, `count-pos.json`).", ""]
    txt = "\n".join(W)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8", newline="\n").write(txt)
    print("written", OUT, len(txt), "max |trade sum - daily| = %.2f" % MAXDIFF[0], file=sys.stderr)


if __name__ == "__main__":
    main()
