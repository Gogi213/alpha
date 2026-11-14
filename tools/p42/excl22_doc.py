"""П-42 §13.4 п. 9: разделы стат-файла «без 22 монет» и «чёрный список walk-forward» (читают report-excl22.json и report-walkfwd.json; счёта нет).

Вызывается из make_stats.py (`section_excl22()`, `section_walkfwd()`); таблица для Excel — `rows_excl22()`.
"""
import json
import os

import make_stats as ms

ROOT = ms.ROOT
WF = os.environ.get("P42_WF") or os.path.join(ROOT, "..", "p42v2-wf")
SIDE_RU = ms.SIDE_RU
ENG_RU = {"tick": "тики, 4 мс", "1m": "минутки"}
EX22 = "CRCLUSDT BILLUSDT PTBUSDT WDCUSDT RLCUSDT WOOUSDT SPKUSDT MTLUSDT ELSAUSDT ARKMUSDT GALAUSDT GPROUSDT PORTALUSDT ANKRUSDT CLOUSDT SLXUSDT TAKEUSDT 1000RATSUSDT CLANKERUSDT HANAUSDT PUMPUSDT SEIUSDT"


def ci(s):
    return "%s [%s; %s]" % (ms.f(s["mean"], 2, True), ms.f(s["lo"], 2, True), ms.f(s["hi"], 2, True))


def load_excl22():
    return json.load(open(os.path.join(ROOT, "report-excl22.json"), encoding="utf-8"))


def rows_excl22():
    """[(сторона, движок, кандидат, универсум, A, B, итого, сделок, вердикт, мелочь)] — для таблиц и Excel."""
    R = load_excl22()
    out = []
    for key, row in R["res"].items():
        eng, side, name = key.split("|")
        for uni in ("full", "excl22"):
            if uni in row:
                c = row[uni]
                out.append((side, eng, name, uni, c["halves"]["A"], c["halves"]["B"], c["total"], c["trades"], c["verdict"]))
    return out


def section_excl22():
    R = load_excl22()
    W = ["## 6. Выборка без 22 монет (приказ владельца; найдено на этих данных)", ""]
    W += ["Слова владельца (дословно): «забей сразу убирай из выборки этих» — 22 монеты: %s. Вселенная 566 → 544, обе стороны, оба движка (минутки шага A и тики 4 мс), все кандидаты (база, перекрёстный, топ-3). Отклонение протокола по слову владельца — §13.4 п. 9." % EX22, "",
          "**Список выбран на этих же 60 сутках (аутсайдеры по клеткам) — найдено на этих данных: числа ниже оптимистичны, в новые сутки они не переносятся автоматически.** Холм (m = лонг %d / шорт %d) выбор списка не покрывает." % (R["m"]["long"], R["m"]["short"]),
          "Исключение — **точный пересчёт** (`--symbols` = 544 монеты: слоты P, пул, паузы пересчитываются, бинарь v11); значения осей кандидатов и топ-3 не перевыбирались. Вердикты — по §10 с устойчивостью, при одном m для обеих вселенных (полновселенные вердикты — как в отчёте v2).", ""]
    ctl = R["control"]
    W += ["Контроль: `b0` на полном списке `--symbols` (566) побайтово равен прежнему — %s." % "; ".join("%s: %s" % (ENG_RU[e], "да" if all(v.values()) else "НЕТ") for e, v in ctl.items()), ""]
    rows = rows_excl22()
    for side in ("long", "short"):
        W += ["### %s" % SIDE_RU[side], ""]
        W += ["| движок | кандидат | вселенная | A $/сут. [95 % интервал] | B $/сут. [95 % интервал] | всего за 60 сут., $ | сделок | вердикт §10 |", "|---|---|---|---|---|---|---|---|"]
        for eng in ("tick", "1m"):
            for r in rows:
                if r[0] != side or r[1] != eng:
                    continue
                _, _, name, uni, a, b, tot, ntr, ver = r
                W.append("| %s | %s | %s | %s | %s | %s | %d | %s |" % (
                    ENG_RU[eng], name, "полная 566" if uni == "full" else "**без 22 (544)**", ci(a), ci(b), ms.f(tot, 2, True), ntr, ver))
        W.append("")
    W += ["### Приближённое исключение против точного (справочно, не испытание)", "",
          "База, вычитание P&L 22 монет из полного ряда против точного пересчёта без них — для оценки смещения приближения §13.7 (слоты P не освобождаются).", "",
          "| сторона, движок | A: полная / точно / приближённо | B: полная / точно / приближённо | сделок: полная / точно / приближённо | P&L 22 монет в базе, $ | макс. расхождение по суткам, $ |", "|---|---|---|---|---|---|"]
    for k, v in R["approx"].items():
        eng, side = k.split("|")
        W.append("| %s, %s | %s / %s / %s | %s / %s / %s | %d / %d / %d | %s | %s |" % (
            SIDE_RU[side], ENG_RU[eng], ms.f(v["full_A"], 2, True), ms.f(v["exact_A"], 2, True), ms.f(v["approx_A"], 2, True),
            ms.f(v["full_B"], 2, True), ms.f(v["exact_B"], 2, True), ms.f(v["approx_B"], 2, True),
            v["trades_full"], v["trades_exact"], v["trades_approx"], ms.f(v["removed_net"], 2, True), ms.f(v["max_abs_day_diff"], 2)))
    W.append("")
    return W


def section_walkfwd():
    p = os.path.join(WF, "report-walkfwd.json")
    if not os.path.exists(p):
        return []
    R = json.load(open(p, encoding="utf-8"))
    W = ["## 7. Чёрный список «неудачников» walk-forward (§13.7)", ""]
    W += ["Слова владельца (через координатора, дословно): «логика такая что если мы знаем про неудачников сразу - мы убираем их и все». Правило записано в протокол до счёта (§13.7): на сутки D исключаются монеты-аутсайдеры («≥ 90 %%», ≥ 5 клеток), посчитанные только по сделкам, закрытым до начала суток D; окна — расширяющееся, 30, 7, 3 суток; прогрев — сутки 0–14 без исключения; исключается база (тики 4 мс и минутки), обе стороны. **Исполнение приближённое** (вычитание P&L монеты за сутки D из суточного ряда; слоты P не освобождаются) — смещение оценено в разделе 6. Набор клеток выбран на всех 60 сутках — утечка набора клеток, не сделок. Новых испытаний +8 на сторону: Холм m = лонг %d / шорт %d." % (R["m"]["long"], R["m"]["short"]), ""]
    for side in ("long", "short"):
        W += ["### %s" % SIDE_RU[side], "",
              "| движок | окно | в чёрном списке в сутки (среднее [мин; макс]) | A $/сут. полная → с исключением | B $/сут. полная → с исключением | Δ с 15-х суток [95 %] | Δ после Холма: нижняя граница | Δ A′ (15–29) / Δ B | всего за 60 сут., $ | убрано сделок | чтение |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for eng in ("tick", "1m"):
            for w in ("expanding", "30", "7", "3"):
                v = R["res"].get("%s|%s|%s" % (eng, side, w))
                if not v:
                    continue
                s = v["seg"]
                W.append("| %s | %s | %s [%d; %d] | %s → %s | %s → %s | %s [%s; %s] | %s | %s / %s | %s → %s | %d из %d | %s |" % (
                    ENG_RU[eng], "расширяющееся" if w == "expanding" else w + " сут.", ms.f(v["bl_mean"], 1), v["bl_min"], v["bl_max"],
                    ms.f(s["A"]["full"]["mean"], 2, True), ms.f(s["A"]["excl"]["mean"], 2, True), ms.f(s["B"]["full"]["mean"], 2, True), ms.f(s["B"]["excl"]["mean"], 2, True),
                    ms.f(s["Δ 15-59"]["mean"], 2, True), ms.f(s["Δ 15-59"]["lo"], 2, True), ms.f(s["Δ 15-59"]["hi"], 2, True), ms.f(s["Δ 15-59"]["low_holm"], 2, True),
                    ms.f(s["Δ A'"]["mean"], 2, True), ms.f(s["Δ B"]["mean"], 2, True), ms.f(v["total_full"], 2, True), ms.f(v["total_excl"], 2, True),
                    v["trades_removed"], v["trades_full"], v["reading"]))
        W.append("")
    return W


def result_bullet():
    """Строка для раздела «Результат»: база на тиках и минутках, полная вселенная → без 22 монет."""
    if not os.path.exists(os.path.join(ROOT, "report-excl22.json")):
        return []
    R = load_excl22()
    parts = []
    for side in ("long", "short"):
        seg = []
        for eng in ("tick", "1m"):
            row = R["res"]["%s|%s|база" % (eng, side)]
            f_, e_ = row["full"], row["excl22"]
            seg.append("%s: A %s / B %s (всего %s $, «%s») → **A %s / B %s (всего %s $, «%s»)**" % (
                ENG_RU[eng], ms.f(f_["halves"]["A"]["mean"], 2, True), ms.f(f_["halves"]["B"]["mean"], 2, True), ms.f(f_["total"], 2, True), f_["verdict"],
                ms.f(e_["halves"]["A"]["mean"], 2, True), ms.f(e_["halves"]["B"]["mean"], 2, True), ms.f(e_["total"], 2, True), e_["verdict"]))
        parts.append("%s — %s" % (SIDE_RU[side].lower(), "; ".join(seg)))
    return ["- **Без 22 монет (приказ владельца, §13.4 п. 9; список выбран на этих же 60 сутках — найдено на этих данных, числа оптимистичны), база, $/сут. A / B:** " + "; ".join(parts) + ". Подробно (все кандидаты, интервалы, точный пересчёт) — раздел 6."]


def wf_bullet():
    """Строка для раздела «Результат»: чёрный список walk-forward (§13.7), Δ с 15-х суток по окнам."""
    p = os.path.join(WF, "report-walkfwd.json")
    if not os.path.exists(p):
        return []
    R = json.load(open(p, encoding="utf-8"))
    parts = []
    for side in ("long", "short"):
        seg = []
        for eng in ("tick", "1m"):
            ws = []
            for w in ("expanding", "30", "7", "3"):
                v = R["res"]["%s|%s|%s" % (eng, side, w)]
                d = v["seg"]["Δ 15-59"]
                ws.append("%s %s [%s; %s]" % ("расш." if w == "expanding" else w, ms.f(d["mean"], 2, True), ms.f(d["lo"], 2, True), ms.f(d["hi"], 2, True)))
            seg.append("%s: %s" % (ENG_RU[eng], ", ".join(ws)))
        parts.append("%s — %s" % (SIDE_RU[side].lower(), "; ".join(seg)))
    reads = {v["reading"] for v in R["res"].values()}
    return ["- **Чёрный список walk-forward (§13.7; приближённое исполнение), Δ $/сут. против полной базы на сутках 15–59, окна расш. / 30 / 7 / 3 сут. [95 %]:** " + "; ".join(parts) + ". Чтение по правилу §13.7 во всех 16 клетках: %s (нижняя граница после Холма < 0 везде). Раздел 7." % ", ".join("«%s»" % x for x in sorted(reads))]


# ---------------- §13.8: перетитровка без 22 монет (полный шаг A и шаг B на 544 монетах)
R544 = os.path.join(ROOT, "..", "p42v2-544")
TAG_RU = {"1m": "минутки (шаг A)", "lx4": "тики, 4 мс (основное)", "lat0": "тики, lat 0 (справочно)", "lx200": "тики, 200 мс (справочно)"}


def load_retitr():
    p = os.path.join(R544, "report-544.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def rows_retitr(tags=("1m", "lx4", "lat0", "lx200")):
    R = load_retitr()
    out = []
    for key, row in R["res"].items():
        tag, side, name = key.split("|")
        if tag not in tags:
            continue
        for uni in ("full", "re544"):
            if uni in row:
                c = row[uni]
                out.append((side, tag, name, uni, c["halves"]["A"], c["halves"]["B"], c["total"], c["trades"], c["verdict"]))
    return out


def pos_counts():
    d = {}
    for lab, r in (("566", ROOT), ("544", R544)):
        p = os.path.join(r, "count-pos.json")
        if os.path.exists(p):
            d[lab] = json.load(open(p, encoding="utf-8"))
    return d


def section_retitr():
    R = load_retitr()
    if R is None:
        return []
    rep = json.load(open(os.path.join(R544, "report.json"), encoding="utf-8"))
    cnt = pos_counts()
    m = R["m"]
    W = ["## 8. Перетитровка без 22 монет (§13.8; найдено на этих данных)", ""]
    W += ["Слова владельца (через координатора, дословно): «перетитруй без этих монет». Вселенная 544 (566 минус 22 монеты раздела 6). Весь шаг A заново на минутках: stage1 (71 вариант) → расширения на краю → совместная сетка двух осей → кандидаты (перекрёстные, «по букве», справочные) — те же скрипты и правила §9/§12, значения осей не переносятся; топ-3 — по правилу §13.1 заново; шаг B — тики 4 мс (основное чтение, §13.4 п. 8) для базы, перекрёстного и топ-3, справочно lat 0 и 200 мс (сигналы общие, дёшево). Точный пересчёт `--symbols` (544), бинарь v11.",
          "**Список выбран на этих же 60 сутках (аутсайдеры по этим же данным), перевыбранные кандидаты — тоже «найдено на этих данных»: числа оптимистичны, на новых сутках не перенесутся автоматически; Холм выбор списка не покрывает.**", ""]
    EQ = R.get("equal_to_excl22", {})
    W += ["**Что выбрал тот же алгоритм на 544:** перекрёстные значения осей — шорт те же, что на 566 (`cA_short`, `cB_short`); лонг: A те же, в B `t1h` = $5 млн вместо $10 млн (оба перекрёстных кандидата лонга, как и на 566, не торгуют — 0 сделок: интервалы {0,0}). Совместная сетка: лонг пары разные в A и B → не принята (как на 566), шорт `be`×`t_low` принята. Расширения на краю: лонг 19 (на 566 — 18), шорт 20. **Топ-3 — те же шесть вариантов, что на 566:** лонг `v_tf_240`, `v_n_bounce_3`, `v_e_0p1`; шорт `v_be_0`, `j_be_0__t_low_2`, `j_be_0__t_low_3` (порядок и числа другие). Поэтому числа базы, перекрёстного шорта и топ-3 совпадают с разделом 6 (те же конфигурации на тех же 544 монетах): проверено побайтово по сделкам, суткам и монето-суткам обеих сторон — {EQ_OK} из {EQ_N} вариантов (минутки и тики 4 мс) равны. Новое в перетитровке — вся сетка (счёт конфигураций, положительных в обеих половинах), подтверждённый перевыбор и накопленный счётчик Холма.", ""]
    W = [x.replace("{EQ_OK}", str(sum(EQ.values()))).replace("{EQ_N}", str(len(EQ))) for x in W]
    W += ["**Испытания:** шаг A — лонг %d, шорт %d (по `report.py`: оси 70 + расширения + совместные клетки + перекрёстный + «по букве»); накопленный Холм m_A = **%d / %d** (227 / 219 прежних + шаг A); шаг B +6 на сторону → **m = %d / %d**, z = %s / %s (одно m для столбцов «566» и «544»; полновселенные вердикты при этом m перечислены в таблицах)." % (
        rep["long"]["trials"], rep["short"]["trials"], rep["long"]["m_package"], rep["short"]["m_package"], m["long"], m["short"],
        ms.f(__import__("statistics").NormalDist().inv_cdf(1 - 0.05 / (2 * m["long"])), 2), ms.f(__import__("statistics").NormalDist().inv_cdf(1 - 0.05 / (2 * m["short"])), 2)), ""]
    W += ["### Сколько конфигураций положительны в обеих половинах (минутки; сетка = stage1 + расширения + совместные клетки, включая базу; описательно)", "",
          "| сторона | вселенная | конфигураций | с торговлей в A и в B | $/сут. > 0 в A | > 0 в B | **> 0 в обеих** | > 0 в обеих и ≥ 10 сделок в каждой |", "|---|---|---|---|---|---|---|---|"]
    for side in ("long", "short"):
        for lab in ("566", "544"):
            if lab not in cnt:
                continue
            v = cnt[lab][side]
            W.append("| %s | %s | %d | %d | %d | %d | **%d** | %d |" % (SIDE_RU[side], "полная 566" if lab == "566" else "**без 22 (544)**", v["configs"], v["trading_both_halves"],
                                                                      v["positive_A"], v["positive_B"], v["positive_both"], v["positive_both_ge10"]))
    W += ["", "Положительность в обеих половинах — по знаку среднего $/сут., без интервалов: на сетке из ~107 конфигураций часть положительных появляется и при нулевом краю (шум), поэтому число сопоставляется с вердиктами §10 ниже, а не заменяет их.", ""]
    rows = rows_retitr()
    for side in ("long", "short"):
        W += ["### %s: кандидаты 566 → 544 (Холм m = %d)" % (SIDE_RU[side], m[side]), ""]
        W += ["| движок | кандидат | вселенная | A $/сут. [95 % интервал] | B $/сут. [95 % интервал] | всего за 60 сут., $ | сделок | вердикт §10 |", "|---|---|---|---|---|---|---|---|"]
        for tag in ("1m", "lx4", "lat0", "lx200"):
            for (sd, tg, name, uni, a, b, tot, ntr, ver) in rows:
                if sd != side or tg != tag:
                    continue
                W.append("| %s | %s | %s | %s | %s | %s | %d | %s |" % (TAG_RU[tag], name, "полная 566" if uni == "full" else "**перетитровка 544**", ci(a), ci(b), ms.f(tot, 2, True), ntr, ver))
        W.append("")
    return W


def retitr_bullet():
    R = load_retitr()
    if R is None:
        return []
    cnt = pos_counts()
    parts = []
    for side in ("long", "short"):
        seg = []
        for tag, lab in (("1m", "минутки"), ("lx4", "тики 4 мс")):
            row = R["res"]["%s|%s|база" % (tag, side)]
            seg.append("%s: %s" % (lab, " → ".join("«%s»" % row[u]["verdict"] for u in ("full", "re544") if u in row)))
        c5, c6 = cnt["544"][side], cnt["566"][side]
        parts.append("%s — конфигураций > 0 в обеих половинах: %d → **%d** из %d (с ≥ 10 сделок: %d → %d); вердикт базы 566 → 544: %s" % (
            SIDE_RU[side].lower(), c6["positive_both"], c5["positive_both"], c5["configs"], c6["positive_both_ge10"], c5["positive_both_ge10"], "; ".join(seg)))
    return ["- **Перетитровка без 22 монет (§13.8; весь шаг A и шаг B заново на 544; найдено на этих данных):** " + "; ".join(parts) + ". Перевыбор дал те же топ-3 и те же перекрёстные значения (кроме `t1h` в B у лонга, кандидат по-прежнему без сделок). Подробно — раздел 8."]
