"""П-42: книга Excel для владельца — сводка по месяцам (лонг и шорт), таблицы по монетам, аутсайдеры. Только основа, тики, задержка 200 мс.

  P42_ROOT=<путь>/data/p42v2 python tools/p42/make_xlsx.py [путь к .xlsx]
Данные те же, что в docs/findings/p42-stats-and-outsiders-2026-11-14.md (раздел 5); счёта нет.
"""
import csv
import os
import sys

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

import coin_month as cmn
import make_stats as ms

OUT = os.path.join("C:\\Users\\Георгий\\Downloads", "P-42-пробой-уровня-статистика.xlsx")
ACCT, POS = cmn.ACCT, cmn.POS
MONTHS = [("2026-09p", "Сентябрь (15–30)"), ("2026-10", "Октябрь"), ("2026-11p", "Ноябрь (1–13)"), ("ALL", "60 дней")]
SIDE_TITLE = {"long": "ЛОНГ", "short": "ШОРТ"}
MONEY = '#,##0.00;[Red]-#,##0.00'
PCT2 = '0.00%;[Red]-0.00%'
PCT3 = '0.000%;[Red]-0.000%'
PCT1 = '0.0%'
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR = PatternFill("solid", fgColor="1F3864")
SUB = PatternFill("solid", fgColor="D9E1F2")
GREY = PatternFill("solid", fgColor="F2F2F2")
WHITE_BOLD = Font(bold=True, color="FFFFFF")


def frac(x, k):
    return None if x is None else x / k


# (подпись, функция r → значение, формат, жирная, заливка % строк)
ROWS = [
    ("Сделок", lambda r: r["n"], "0", False, False),
    ("Сделок в сутки", lambda r: r["n"] / r["nd"], "0.00", False, False),
    ("Доля прибыльных сделок, %", lambda r: r.get("win"), PCT1, False, False),
    ("Средняя прибыльная сделка, $", lambda r: r.get("avg_win"), MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: frac(r.get("avg_win"), ACCT), PCT3, False, True),
    ("Средняя убыточная сделка, $", lambda r: r.get("avg_loss"), MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: frac(r.get("avg_loss"), ACCT), PCT3, False, True),
    ("Средняя сделка, $", lambda r: r.get("avg"), MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: frac(r.get("avg"), ACCT), PCT3, False, True),
    ("Профит-фактор", lambda r: r.get("pf"), "0.00", False, False),
    ("Итого за период, $", lambda r: r["total"], MONEY, True, False),
    ("   то же, % от счёта $2 500 (доходность)", lambda r: r["total"] / ACCT, PCT2, True, True),
    ("В среднем за сутки, $", lambda r: r["total"] / r["nd"], MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: r["total"] / r["nd"] / ACCT, PCT3, False, True),
    ("Максимальная просадка, $", lambda r: -r["dd"], MONEY, False, False),
    ("   то же, % (от пика счёта)", lambda r: -r["ddp"], PCT2, True, True),
    ("Дольше всего без нового максимума счёта, сут.", lambda r: r["longest"], "0", False, False),
    ("   из них не закрыто к концу периода, сут.", lambda r: r["tail"], "0", False, True),
    ("Худшие сутки, $", lambda r: r["worst"], MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: frac(r["worst"], ACCT), PCT2, False, True),
    ("Лучшие сутки, $", lambda r: r["best"], MONEY, False, False),
    ("   то же, % от счёта $2 500", lambda r: frac(r["best"], ACCT), PCT2, False, True),
    ("Закрыто по стопу, % сделок", lambda r: r.get("sh_stop"), PCT1, False, False),
    ("Закрыто по безубытку, % сделок", lambda r: r.get("sh_be"), PCT1, False, False),
    ("Закрыто по трейлу, % сделок", lambda r: r.get("sh_trail"), PCT1, False, False),
    ("Закрыто тейками (исполнены все), % сделок", lambda r: r.get("sh_take"), PCT1, False, False),
    ("Среднее время в сделке, мин", lambda r: r.get("hold"), "#,##0", False, False),
    ("   медиана времени в сделке, мин", lambda r: r.get("hold_med"), "0.0", False, True),
]


def put(ws, row, col, val, fmt=None, bold=False, fill=None, align=None, font=None):
    c = ws.cell(row=row, column=col, value=("—" if val is None else val))
    if fmt and val is not None:
        c.number_format = fmt
    c.border = BOX
    if bold:
        c.font = Font(bold=True)
    if font:
        c.font = font
    if fill:
        c.fill = fill
    c.alignment = align or Alignment(horizontal="right" if val is not None else "center", vertical="center")
    return c


def summary(wb, pdata):
    ws = wb.active
    ws.title = "Сводка"
    lg, sh = pdata[("long", "tick", "ALL")], pdata[("short", "tick", "ALL")]
    notes = [
        "Что это: статистика бота «пробой часового уровня» (П-42) по сделкам Binance за 60 дней (15.09–13.11.2026): правила бота как есть, исполнение по тикам с задержкой 200 мс, 566 монет; лонг и шорт — отдельно.",
        "Счёт $2 500, позиция $500 на сделку, без плеча; проценты — от счёта $2 500, просадка — от пика счёта; сентябрь (15–30) и ноябрь (1–13) — неполные месяцы. Всё — на данных этого окна, не прогноз.",
        "Вердикт: ЛОНГ — «не ясно»: около нуля (%s $ за 60 дней, %s %% счёта), плюс или минус на этих данных не доказать; ШОРТ — «опровергнуто»: %s $ за 60 дней (%s %% счёта), минус в обеих половинах окна."
        % (ms.f(lg["total"], 0), ms.f(lg["total"] / ACCT * 100, 1), ms.f(sh["total"], 0), ms.f(sh["total"] / ACCT * 100, 1)),
    ]
    for i, t in enumerate(notes, 1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=10)
        c = ws.cell(row=i, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.font = Font(bold=(i == 3), size=11)
        ws.row_dimensions[i].height = 34
    top = 5
    ws.cell(row=top, column=1, value="Метрика").font = WHITE_BOLD
    ws.cell(row=top + 1, column=1).fill = HDR
    ws.merge_cells(start_row=top, start_column=1, end_row=top + 1, end_column=1)
    for c in (ws.cell(row=top, column=1),):
        c.fill, c.border = HDR, BOX
        c.alignment = Alignment(horizontal="center", vertical="center")
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
            "• Профит-фактор — сумма прибыльных сделок, делённая на сумму убыточных; больше 1 — период в плюсе.",
            "• Просадка — наибольшее падение счёта от его пика внутри периода (по суткам), в $ и в % от пика счёта (2 500 + накопленный итог на пике).",
            "• «Без нового максимума» — самый долгий период (сутки), пока счёт не обновил свой пик; «не закрыто к концу» — сколько суток прошло с последнего пика до конца периода.",
            "• Выходы: стоп — жёсткий стоп до первой фиксации прибыли; безубыток — остаток закрыт в ноль после первой фиксации; трейл — остаток закрыт скользящим стопом; тейки — исполнены все 8 частичных фиксаций.",
            "• Число сделок — по дню входа; итог, просадка и сутки — по суточному ряду. Месяцы неполные: сентябрь — 16 суток, ноябрь — 13."]
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
    ws.sheet_view.zoomScale = 100


COIN_COLS = [("Сделок", "0"), ("Прибыльных, %", "0%"), ("Итог, $", MONEY), ("Итог, % счёта", PCT2), ("Макс. просадка, %", "0.00%"), ("Профит-фактор", "0.00")]


def coin_sheet(wb, side, cm):
    ws = wb.create_sheet("%s — монеты" % ms.SIDE_RU[side])
    coins = sorted({c for (s, e, c, lab) in cm if s == side and e == "tick" and lab == "ALL"},
                   key=lambda c: (cm[(side, "tick", c, "ALL")]["total"], c))
    ws.cell(row=1, column=1, value="Монета")
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    for j, (lab, name) in enumerate(MONTHS):
        c0 = 2 + j * len(COIN_COLS)
        ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c0 + len(COIN_COLS) - 1)
        g = ws.cell(row=1, column=c0, value=name)
        g.font, g.fill, g.alignment = WHITE_BOLD, HDR, Alignment(horizontal="center")
        for k, (h, _) in enumerate(COIN_COLS):
            c = ws.cell(row=2, column=c0 + k, value=h)
            c.font, c.fill, c.border = Font(bold=True), SUB, BOX
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    a = ws.cell(row=1, column=1)
    a.font, a.fill, a.border = WHITE_BOLD, HDR, BOX
    a.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 32
    for i, coin in enumerate(coins):
        r = 3 + i
        put(ws, r, 1, coin, align=Alignment(horizontal="left"))
        for j, (lab, _) in enumerate(MONTHS):
            x = cm.get((side, "tick", coin, lab))
            c0 = 2 + j * len(COIN_COLS)
            if x is None:
                for k in range(len(COIN_COLS)):
                    put(ws, r, c0 + k, None)
                continue
            vals = [x["n"], x.get("win"), x["total"], x["total"] / ACCT, x["ddp"], x.get("pf")]
            for k, v in enumerate(vals):
                put(ws, r, c0 + k, v, COIN_COLS[k][1])
    last = 2 + len(coins)
    red = dict(fill=PatternFill("solid", bgColor="FFC7CE", fgColor="FFC7CE"), font=Font(color="9C0006"))
    green = dict(fill=PatternFill("solid", bgColor="C6EFCE", fgColor="C6EFCE"), font=Font(color="006100"))
    for j in range(len(MONTHS)):
        c0 = 2 + j * len(COIN_COLS)
        rng = "%s3:%s%d" % (L(c0 + 2), L(c0 + 3), last)
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], **red))
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"], **green))
    ws.freeze_panes = "B3"
    ws.auto_filter.ref = "A2:%s%d" % (L(1 + len(MONTHS) * len(COIN_COLS)), last)
    ws.column_dimensions["A"].width = 22
    for c in range(2, 2 + len(MONTHS) * len(COIN_COLS)):
        ws.column_dimensions[L(c)].width = 12
    return len(coins)


def outsiders_sheet(wb):
    ws = wb.create_sheet("Аутсайдеры")
    notes = ["Аутсайдер — монета, у которой во всех вариантах правил бота («клетках»), где она торговалась, итог отрицательный (минус на всех клетках). Здесь — тики, класс «100 %», только монеты с ≥ 5 клетками.",
             "Сделки и итог — суммы по клеткам: одна и та же сделка, попавшая в несколько вариантов, считается в каждом. Найдено на этих данных (15.09–13.11): в другой половине окна список не воспроизводится, это не фильтр для бота."]
    for i, t in enumerate(notes, 1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        c = ws.cell(row=i, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[i].height = 44
    r = 4
    for side in ms.SIDES:
        rows = [x for x in csv.DictReader(open(os.path.join(ms.ROOT, "outsiders-%s.csv" % side), encoding="utf-8")) if x["engine"] == "tick" and x["class"] == "100"]
        rows.sort(key=lambda x: float(x["net_sum_cells"]))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        t = ws.cell(row=r, column=1, value="%s: %d монет в минусе на всех клетках (тики)" % (SIDE_TITLE[side], len(rows)))
        t.font, t.fill = WHITE_BOLD, HDR
        r += 1
        for k, h in enumerate(["№", "Монета", "Клеток в минусе", "Клеток всего", "Сделок (сумма по клеткам)", "Итог, $ (сумма по клеткам)"], 1):
            c = ws.cell(row=r, column=k, value=h)
            c.font, c.fill, c.border = Font(bold=True), SUB, BOX
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 32
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


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    pdata, _coins, _diff, _cnt, cm = cmn.run()
    wb = Workbook()
    summary(wb, pdata)
    n = {s: coin_sheet(wb, s, cm) for s in ms.SIDES}
    outsiders_sheet(wb)
    wb.save(out)
    print("saved", out, n, file=sys.stderr)


if __name__ == "__main__":
    main()
