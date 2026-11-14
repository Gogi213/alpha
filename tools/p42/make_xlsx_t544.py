"""П-42 §13.9: итоговая книга Excel для владельца — только тики 4 мс, 544 монеты; кандидаты из тиковой сетки; минутки — один справочный лист.

  P42_ROOT=data/p42v2-t544 python tools/p42/make_xlsx_t544.py [путь к .xlsx]
Листы: Сводка (вердикт и месяцы), Лонг — монеты, Шорт — монеты, Аутсайдеры, Варианты, Минутки (справочно). Данные: data/p42v2-t544 (сетка на тиках), report-tgrid.json; счёта нет.
"""
import csv
import json
import os
import sys

os.environ["P42_ROOT"] = os.environ.get("P42_ROOT", "data/p42v2-t544")
sys.path.insert(0, os.path.dirname(__file__))
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402

import coin_month as cmn  # noqa: E402
import excl22_doc as ed  # noqa: E402
import make_stats as ms  # noqa: E402
import make_xlsx as mx  # noqa: E402

OUT = os.path.join("C:\\Users\\Георгий\\Downloads", "P-42-тики-4мс-544.xlsx")
ROOT = ms.ROOT
cmn.ENG = (("tick", "stage1/b0", "тики, 4 мс, 544 монеты"),)   # база тиковой сетки
cmn.DIR = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "findings", "p42-t544-per-coin-month")
put, HDR, SUB, GREY, WHITE_BOLD, MONEY, BOX = mx.put, mx.HDR, mx.SUB, mx.GREY, mx.WHITE_BOLD, mx.MONEY, mx.BOX
GREEN = PatternFill("solid", fgColor="E2EFDA")
YELLOW = PatternFill("solid", fgColor="FFF2CC")
MONTHS, SIDE_TITLE, ROWS, ACCT = mx.MONTHS, mx.SIDE_TITLE, mx.ROWS, mx.ACCT


def hdr_row(ws, r, heads, h=32):
    for k, t in enumerate(heads, 1):
        c = ws.cell(row=r, column=k, value=t)
        c.font, c.fill, c.border = Font(bold=True), SUB, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[r].height = h


def title_row(ws, r, text, ncol=12):
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
    t = ws.cell(row=r, column=1, value=text)
    t.font, t.fill, t.alignment = WHITE_BOLD, HDR, Alignment(wrap_text=True, vertical="center")
    return t


def verdict_line(R5):
    parts = []
    for side in ms.SIDES:
        x = R5["sides"][side]
        b = x["cand"]["база"]
        a, bb = b["halves"]["A"], b["halves"]["B"]
        v = ed.vtxt(b["verdict"], a, bb)
        c = x["cand"].get("перекрёстный")
        s1 = x["cand"].get("стоп 1 % (v_s_1)")
        cv = (ed.vtxt(c["verdict"], c["halves"]["A"], c["halves"]["B"]) if c and c["trades"] else "не торгует")
        tops = ", ".join("«%s»" % ed.vtxt(k_["verdict"], k_["halves"]["A"], k_["halves"]["B"]) for k, k_ in x["cand"].items() if k.startswith("топ-3"))
        parts.append("%s — основа «%s»: %s $ за 60 суток (%s $ в сутки в первой половине окна, %s — во второй); перекрёстный кандидат (принятие осей по обеим половинам — в выборке) «%s»; три лучших варианта сетки: %s%s%s" % (
            SIDE_TITLE[side], v, ms.f(b["total"], 0, True), ms.f(a["mean"], 2, True), ms.f(bb["mean"], 2, True), cv, tops,
            ("; стоп 1 %% (слова владельца «1 тоже попробуй»): «%s», %s $ за 60 суток" % (ed.vtxt(s1["verdict"], s1["halves"]["A"], s1["halves"]["B"]), ms.f(s1["total"], 0, True))) if s1 else "", ed.hon_txt(side, R5, ed.load_tgrid("566"))))
    return "Вердикт по правилу проверки (тики, задержка 4 мс, 544 монеты, 60 суток). " + "; ".join(parts) + "."


def summary_sheet(wb, pdata, R5, R6):
    ws = wb.active
    ws.title = "Сводка"
    notes = [
        "Что это: статистика бота «пробой часового уровня» (П-42) по сделкам Binance за 60 суток (15.09–13.11.2026): правила бота как есть, исполнение по тикам с задержкой 4 мс на вход и выход; 544 монеты (566 минус 22 монеты, которые владелец велел убрать); лонг и шорт — отдельно. Счёт $2 500, позиция $500 на сделку, без плеча; проценты — от счёта.",
        "НАЙДЕНО НА ЭТИХ ДАННЫХ — числа оптимистичны: 22 монеты убраны по результатам этого же окна, а варианты (кандидаты) выбраны по этим же 60 суткам. Прирост базы относительно 566 монет — это P&L убранных монет, отобранных как убыточные на этих же сделках: по построению, не находка. Чёрный список без заглядывания вперёд (walk-forward) прироста не даёт — «в шуме» во всех 16 проверках. «Перекрёстный» кандидат принимает ось по обеим половинам окна (на 566 у лонга оба конфига совпадают), поэтому его число — тоже в выборке; без заглядывания в проверочную половину — «честный эталон» (§13.10, лист «Варианты»).",
        verdict_line(R5),
    ]
    for i, t in enumerate(notes, 1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=10)
        c = ws.cell(row=i, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.font = Font(bold=(i in (2, 3)), size=11)
        ws.row_dimensions[i].height = 62 if i == 3 else 48
    top = 5
    ws.cell(row=top, column=1, value="Метрика (основа, тики 4 мс, 544 монеты)").font = WHITE_BOLD
    ws.merge_cells(start_row=top, start_column=1, end_row=top + 1, end_column=1)
    c = ws.cell(row=top, column=1)
    c.fill, c.border, c.alignment = HDR, BOX, Alignment(horizontal="center", vertical="center", wrap_text=True)
    for side, c0 in (("long", 2), ("short", 7)):
        ws.merge_cells(start_row=top, start_column=c0, end_row=top, end_column=c0 + 3)
        t = ws.cell(row=top, column=c0, value=SIDE_TITLE[side])
        t.font, t.fill, t.alignment = WHITE_BOLD, HDR, Alignment(horizontal="center")
        for j, (lab, name) in enumerate(MONTHS):
            h = ws.cell(row=top + 1, column=c0 + j, value=name)
            h.font, h.fill, h.border = Font(bold=True), SUB, BOX
            h.alignment = Alignment(horizontal="center", wrap_text=True)
    r0 = top + 2
    for i, (lab, fn, fmt, bold, shade) in enumerate(ROWS):
        r = r0 + i
        put(ws, r, 1, lab, bold=bold, fill=GREY if shade else None, align=Alignment(horizontal="left", vertical="center"))
        for side, c0 in (("long", 2), ("short", 7)):
            for j, (mlab, _) in enumerate(MONTHS):
                put(ws, r, c0 + j, fn(pdata[(side, "tick", mlab)]), fmt, bold, GREY if shade else None)
    r = r0 + len(ROWS) + 1
    expl = ["Как читать:",
            "• Основа — правила бота без подбора. Кандидаты (перекрёстный и три лучших варианта сетки) — на листе «Варианты». Сетка: 107 вариантов правил на сторону, все посчитаны на тиках; сколько из них в плюсе в обеих половинах окна — там же.",
            "• Вердикт: «не ясно» — плюс или минус не доказать (интервал накрывает 0); «опровергнуто» — стабильный минус, держится без 2 худших суток и без 2 худших монет; «подтверждено» — стабильный плюс (нет ни у одного кандидата). Пометка «пограничное» — минус почти у нуля после отбрасывания худших суток/монет.",
            "• Профит-фактор — сумма прибыльных сделок, делённая на сумму убыточных; больше 1 — период в плюсе. Просадка — наибольшее падение счёта от пика (по суткам). Месяцы неполные: сентябрь — 16 суток, ноябрь — 13.",
            "• Минутки (старый упрощённый движок) — один справочный лист, на выбор вариантов не влияют."]
    for k, t in enumerate(expl):
        ws.merge_cells(start_row=r + k, start_column=1, end_row=r + k, end_column=10)
        c = ws.cell(row=r + k, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(bold=(k == 0), italic=(k > 0), size=10)
        ws.row_dimensions[r + k].height = 30 if k > 0 else 16
    ws.column_dimensions["A"].width = 50
    for c in "BCDEGHIJ":
        ws.column_dimensions[c].width = 16
    ws.column_dimensions["F"].width = 3
    ws.freeze_panes = ws.cell(row=r0, column=2)


def outsiders_sheet(wb):
    ws = wb.create_sheet("Аутсайдеры")
    notes = ["Аутсайдер — монета, у которой во всех вариантах правил («клетках»), где она торговалась, итог отрицательный. Клетки — вся тиковая сетка (107 вариантов, 4 мс, 544 монеты); берутся монеты, у которых ≥ 5 клеток со сделками; класс «100 %» — минус во всех клетках.",
             "Сделки и итог — суммы по клеткам: одна сделка, попавшая в несколько вариантов, считается в каждом. Найдено на этих данных: список описывает это окно, в другой половине окна он не воспроизводится — не фильтр для бота."]
    for i, t in enumerate(notes, 1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        c = ws.cell(row=i, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[i].height = 48
    r = 4
    for side in ms.SIDES:
        f = os.path.join(ROOT, "outsiders-%s.csv" % side)
        rows = [x for x in csv.DictReader(open(f, encoding="utf-8")) if x["class"] == "100"] if os.path.exists(f) else []
        rows.sort(key=lambda x: float(x["net_sum_cells"]))
        title_row(ws, r, "%s: %d монет в минусе на всех клетках (тики 4 мс, 544)" % (SIDE_TITLE[side], len(rows)), 6)
        r += 1
        hdr_row(ws, r, ["№", "Монета", "Клеток в минусе", "Клеток всего", "Сделок (сумма по клеткам)", "Итог, $ (сумма по клеткам)"])
        r += 1
        for i, x in enumerate(rows, 1):
            put(ws, r, 1, i, "0")
            put(ws, r, 2, ms.fixname(x["sym"]), align=Alignment(horizontal="left"))
            put(ws, r, 3, int(x["cells_neg"]), "0")
            put(ws, r, 4, int(x["cells"]), "0")
            put(ws, r, 5, int(x["trades_sum_cells"]), "0")
            put(ws, r, 6, float(x["net_sum_cells"]), MONEY)
            r += 1
        r += 2
    for c, w in zip("ABCDEF", (6, 24, 16, 14, 22, 24)):
        ws.column_dimensions[c].width = w


def variants_sheet(wb, R5, R6):
    ws = wb.create_sheet("Варианты")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=12)
    c = ws.cell(row=1, column=1, value="Кандидаты и вся сетка вариантов (тики 4 мс). Найдено на этих данных: варианты выбраны по этим же 60 суткам — оптимистично. ВНИМАНИЕ: «перекрёстный» принимает ось, только если разница > 0 в обе стороны, т. е. по обеим половинам — его число НЕ вне выборки (читать как топ-3); вне выборки — строка «честный эталон (§13.10)»: значения и принятие осей только на половине подбора. Интервал — 95 % без поправки на число испытаний, вердикт — после Холма; «без 2 худших» — среднее $/сут. половины без 2 худших суток / без 2 худших монет.")
    c.alignment = Alignment(wrap_text=True, vertical="center")
    c.font = Font(bold=True)
    ws.row_dimensions[1].height = 48
    r = 3
    title_row(ws, r, "Сколько вариантов сетки положительны в обеих половинах окна (знак среднего $/сут., без интервалов; из них с ≥ 10 сделок в каждой половине)")
    r += 1
    hdr_row(ws, r, ["Сторона", "Вселенная", "Вариантов в сетке", "С торговлей в A и в B", "> 0 в A", "> 0 в B", "> 0 в обеих", "> 0 в обеих и ≥ 10 сделок"])
    r += 1
    for side in ms.SIDES:
        for lab, R in (("544", R5), ("566", R6)):
            if R is None:
                continue
            cc = R["sides"][side]["counts"]
            for k, v in enumerate([SIDE_TITLE[side], "без 22 (544)" if lab == "544" else "полная (566)", cc["configs"], cc["trading_both_halves"], cc["positive_A"], cc["positive_B"], cc["positive_both"], cc["positive_both_ge10"]], 1):
                put(ws, r, k, v, "0" if k > 2 else None, bold=(k == 7), fill=GREEN if lab == "544" else None, align=Alignment(horizontal="left" if k <= 2 else "right"))
            r += 1
    r += 2
    heads = ["Кандидат", "Вселенная", "A: $ в сутки", "A: от", "A: до", "A: без 2 худших суток / монет", "B: $ в сутки", "B: от", "B: до", "B: без 2 худших суток / монет", "Всего за 60 сут., $", "Сделок", "Вердикт", "без 2 лучших суток / монет (A; B), если обе половины > 0"]
    for side in ms.SIDES:
        title_row(ws, r, "%s — кандидаты (Холм m = %d)" % (SIDE_TITLE[side], R5["sides"][side]["m"]), 14)
        r += 1
        hdr_row(ws, r, heads)
        r += 1
        names = list(R5["sides"][side]["cand"])
        for lab, R in (("544", R5), ("566", R6)):
            if R is None:
                continue
            for name in names + [n for n in R["sides"][side]["cand"] if n not in names]:
                cd = R["sides"][side]["cand"].get(name)
                if cd is None:
                    continue
                a, b = cd["halves"]["A"], cd["halves"]["B"]
                vals = [("перекрёстный (в выборке)" if name == "перекрёстный" else name), "без 22 (544)" if lab == "544" else "полная (566)", a["mean"], a["lo"], a["hi"], ed.rob(a), b["mean"], b["lo"], b["hi"], ed.rob(b), cd["total"], cd["trades"],
                        ed.vtxt(cd["verdict"], a, b), ed.best2(a, b)]
                for k, v in enumerate(vals, 1):
                    put(ws, r, k, v, MONEY if k in (3, 4, 5, 7, 8, 9, 11) else ("0" if k == 12 else None), bold=(lab == "544" and k in (3, 7, 11)), fill=GREEN if lab == "544" else None,
                        align=Alignment(horizontal="left" if k in (1, 2, 6, 10, 13, 14) else "right"))
                r += 1
        r += 1
        for lab, R in (("544", R5), ("566", R6)):
            if R is None:
                continue
            x = R["sides"][side]
            rows_cfg = [("перекрёстный (в выборке): значения из B (для суток A)", x["cfg"]["B"]), ("перекрёстный (в выборке): значения из A (для суток B)", x["cfg"]["A"])]
            if x.get("cfg_honest"):
                rows_cfg += [("честный эталон: выбран на B (считается на A)", x["cfg_honest"]["B"]), ("честный эталон: выбран на A (считается на B)", x["cfg_honest"]["A"])]
            for lab_, hh in rows_cfg:
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
                c = ws.cell(row=r, column=1, value="%s, %s: %s" % (lab, lab_, ed.cfg_txt(hh)))
                c.font = Font(italic=True, size=10)
                r += 1
        r += 2
    sn = ed.sig_note()
    if sn:
        title_row(ws, r, "Почему «не ясно», хотя 95 %-интервал выше 0 в обеих половинах (до поправки на число испытаний)", 14)
        r += 1
        for t in sn:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
            c = ws.cell(row=r, column=1, value=t)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 46
            r += 1
        r += 2
    for side in ms.SIDES:
        title_row(ws, r, "%s — вся сетка на 544 монетах: %d вариантов (жёлтым — положительны в обеих половинах)" % (SIDE_TITLE[side], len(R5["sides"][side]["grid"])), 13)
        r += 1
        hdr_row(ws, r, ["Вариант", "Что изменено против основы", "A: $ в сутки", "B: $ в сутки", "Сделок A", "Сделок B", "Всего за 60 сут., $", "Плюс в обеих"], 32)
        r += 1
        for g in sorted(R5["sides"][side]["grid"], key=lambda g: -min(g["A"], g["B"])):
            both = g["A"] > 0 and g["B"] > 0
            for k, v in enumerate([g["id"], g["params"] or "основа", g["A"], g["B"], g["nA"], g["nB"], g["total"], "да" if both else ""], 1):
                put(ws, r, k, v, MONEY if k in (3, 4, 7) else ("0" if k in (5, 6) else None), fill=YELLOW if both else None, align=Alignment(horizontal="left" if k in (1, 2, 8) else "right"))
            r += 1
        r += 2
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 34
    for c in "CDEFGHIJKLM":
        ws.column_dimensions[c].width = 15
    ws.column_dimensions["N"].width = 34
    ws.freeze_panes = "A3"


def minutes_sheet(wb):
    ws = wb.create_sheet("Минутки (справочно)")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=11)
    c = ws.cell(row=1, column=1, value="СПРАВОЧНО. Минутки — прежний упрощённый движок (вход и выход по минутным свечам, без задержки). На выбор вариантов не влияют: владелец — «а нахуй тебе минутки». Здесь — те же варианты (основа, перекрёстный, три лучших), выбранные на минутках, посчитанные на минутках, 544 монеты. Найдено на этих данных.")
    c.alignment = Alignment(wrap_text=True, vertical="center")
    c.font = Font(bold=True)
    ws.row_dimensions[1].height = 62
    R = ed.load_retitr()
    cnt = ed.pos_counts().get("544")
    r = 3
    if cnt:
        hdr_row(ws, r, ["Сторона", "Вариантов в сетке", "> 0 в A", "> 0 в B", "> 0 в обеих", "> 0 в обеих и ≥ 10 сделок"])
        r += 1
        for side in ms.SIDES:
            v = cnt[side]
            for k, val in enumerate([SIDE_TITLE[side], v["configs"], v["positive_A"], v["positive_B"], v["positive_both"], v["positive_both_ge10"]], 1):
                put(ws, r, k, val, "0" if k > 1 else None, align=Alignment(horizontal="left" if k == 1 else "right"))
            r += 1
        r += 2
    rows = [x for x in ed.rows_retitr(("1m",)) if x[3] == "re544"]
    hdr_row(ws, r, ["Сторона", "Кандидат", "A: $ в сутки", "A: от", "A: до", "B: $ в сутки", "B: от", "B: до", "Всего за 60 сут., $", "Сделок", "Вердикт"])
    r += 1
    for (sd, tg, name, uni, a, b, tot, ntr, ver) in rows:
        for k, v in enumerate([SIDE_TITLE[sd], name, a["mean"], a["lo"], a["hi"], b["mean"], b["lo"], b["hi"], tot, ntr, ed.vtxt(ver, a, b)], 1):
            put(ws, r, k, v, MONEY if k in (3, 4, 5, 6, 7, 8, 9) else ("0" if k == 10 else None), align=Alignment(horizontal="left" if k in (1, 2, 11) else "right"))
        r += 1
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 30
    for c in "CDEFGHIJK":
        ws.column_dimensions[c].width = 15
    ws.column_dimensions["K"].width = 26


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    R5, R6 = ed.load_tgrid("544"), ed.load_tgrid("566")
    pdata, _coins, _diff, _cnt, cm = cmn.run()
    wb = Workbook()
    summary_sheet(wb, pdata, R5, R6)
    n = {s: mx.coin_sheet(wb, s, cm) for s in ms.SIDES}
    outsiders_sheet(wb)
    variants_sheet(wb, R5, R6)
    minutes_sheet(wb)
    wb.save(out)
    print("saved", out, n, file=sys.stderr)


if __name__ == "__main__":
    main()
