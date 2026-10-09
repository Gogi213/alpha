#!/usr/bin/env python3
"""TK-113 шаг 1: разрез итога $ по монетам и месяцам — база B1 и лучшие клетки (g93-u1-K3, g94-n4), без потолка (free) и B2 (потолок 3).
Вход data/tk113/cs-cap{0,3}-<мес>.json (portfolio-sim --closes-sym-out, равная экспозиция §6(2) — vn-b, как p12-r2-analyze NORM=1);
выход docs/findings/coins-breakdown-2026-10-09.{csv,md}. Описание, не вердикт: пороги классов ниже — только для чтения таблицы."""
import csv
import datetime as dt
import json
import sys
from collections import defaultdict

D = "data/tk113/"
B = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
S = "t-bid-btc4h-q1@"
CELLS = {"B1": S + B, "g93-u1-K3": S + B + "-pynw3u3", "g94-n4": S + B + "-pyeat4"}
MODES = {"free": "0", "B2": "3"}
MONTHS = [f"2026-{m:02d}" for m in range(2, 10)]   # единое окно П-12 §4: фев–сен (янв — калибровка, окт неполный: в итоги не входят)
MIN_TR = 10      # монета «в счёте» — не меньше 10 сделок у базы за период (порог чтения, не вердикт)
FRAC = 2 / 3     # «большинство месяцев»

usd = defaultdict(float)   # (режим, клетка, монета, месяц)
ntr = defaultdict(int)
wins = defaultdict(int)
for mode, cap in MODES.items():
    for m in MONTHS:
        j = json.load(open(f"{D}cs-cap{cap}-{m}.json", encoding="utf-8"))
        for cell, form in CELLS.items():
            for _p, caps in j.get(form, {}).items():
                for _c, lst in caps.items():
                    for _ms, v, sym in lst:
                        usd[mode, cell, sym, m] += v
                        ntr[mode, cell, sym, m] += 1
                        wins[mode, cell, sym, m] += v > 0

coins = sorted({k[2] for k in usd})
with open("docs/findings/coins-breakdown-2026-10-09.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["mode", "cell", "coin", "month", "trades", "usd"])
    for (mode, cell, sym, m), v in sorted(usd.items()):
        w.writerow([mode, cell, sym, m, ntr[mode, cell, sym, m], round(v, 2)])


def tot(mode, cell, c):
    return sum(usd.get((mode, cell, c, m), 0.0) for m in MONTHS), sum(ntr.get((mode, cell, c, m), 0) for m in MONTHS)


def posfrac(mode, cell, c):
    act = [m for m in MONTHS if ntr.get((mode, cell, c, m), 0)]
    return (sum(1 for m in act if usd[mode, cell, c, m] > 0), len(act))


keys = [(mo, ce) for mo in MODES for ce in CELLS]
rows = []
for c in coins:
    t = {k: tot(*k, c) for k in keys}
    pf = {k: posfrac(*k, c) for k in keys}
    nb = t[("free", "B1")][1]
    rows.append((c, t, pf, nb))

lines = ["# Где не работает ничего: разрез по монетам и месяцам (фев–сен 2026, единое окно П-12 §4)", "",
         "Шаг 1 TK-113, **описание, не вердикт**. Источник: сделки portfolio-sim по пулу v171b (волна TK-065, равная экспозиция §6(2)), "
         "$ при позиции 500 $, депозит 2500 $. Клетки: B1 (база), g93-u1-K3, g94-n4; режимы free (без потолка) и B2 (потолок 3). "
         f"Порог чтения: монета «в счёте» при >= {MIN_TR} сделок базы (free); «большинство месяцев» = >= {FRAC:.0%} месяцев со сделками. "
         "Построчно — `coins-breakdown-2026-10-09.csv` (режим, клетка, монета, месяц, сделок, $).", ""]
lines += ["## Итого по клеткам", "", "| режим | клетка | сделок | $ | монет | монет в плюсе |", "|---|---|---|---|---|---|"]
for mo, ce in keys:
    tt = sum(tot(mo, ce, c)[0] for c in coins)
    nn = sum(tot(mo, ce, c)[1] for c in coins)
    npos = sum(1 for c in coins if tot(mo, ce, c)[1] and tot(mo, ce, c)[0] > 0)
    nco = sum(1 for c in coins if tot(mo, ce, c)[1])
    lines.append(f"| {mo} | {ce} | {nn} | {tt:+.0f} | {nco} | {npos} |")

# (а) не работает ничего: минус по итогу во всех 6 комбинациях (заказ владельца 04:20); месячный признак — столбцом, не фильтром
bad, good = [], []
for c, t, pf, nb in rows:
    act = [k for k in keys if t[k][1]]
    if len(act) == len(keys) and all(t[k][0] < 0 for k in act):
        bad.append(c)
    elif nb >= MIN_TR and all(t[k][0] > 0 for k in act) and len(act) == len(keys) and all(pf[k][0] >= pf[k][1] * FRAC for k in act):
        good.append(c)
DROPPED = {"TRUMPUSDT", "TRXUSDT", "BCHUSDT"}


def stat(mo, ce, c):
    act = [m for m in MONTHS if ntr.get((mo, ce, c, m), 0)]
    n = sum(ntr[mo, ce, c, m] for m in act)
    w = sum(wins[mo, ce, c, m] for m in act)
    mu = [usd[mo, ce, c, m] for m in act]
    return (sum(mu), n, w / n if n else 0, sum(1 for x in mu if x < 0), sum(1 for x in mu if x > 0), min(mu) if mu else 0)


def line(c):
    t, n, w, neg, pos, worst = stat("free", "B1", c)
    rest = " | ".join(f"{stat(mo, ce, c)[0]:+.0f}" for mo, ce in keys[1:])
    mark = ("†" if c in DROPPED else "") + ("" if n >= MIN_TR else " (<10 сд.)")
    return f"| {c}{mark} | {t:+.0f} | {n} | {w:.0%} | {neg}/{pos} | {worst:+.0f} | {rest} |"


hdr = ("| монета | $ B1 free | сделок | доля прибыльных | мес.− / мес.+ | худший мес. $ | "
       + " | ".join(f"$ {mo} {ce}" for mo, ce in keys[1:]) + " |")
sep = "|---|" + "---|" * (len(keys) + 4)
lines += ["", "Колонки B1 free — полная статистика монеты (мес.−/мес.+ среди месяцев со сделками); остальные — итог $ за период.", "",
          f"## (а) Не работает ничего — минус по итогу во всех 6 комбинациях ({len(bad)}; с >= {MIN_TR} сделок: {sum(1 for c in bad if tot('free', 'B1', c)[1] >= MIN_TR)}; † — исключены В-210)", "", hdr, sep]
lines += [line(c) for c in sorted(bad, key=lambda c: tot("free", "B1", c)[0])] or ["| — |"]
lines += ["", f"## (б) Стабильно в плюсе — плюс по итогу во всех 6 комбинациях и в >= {FRAC:.0%} месяцев каждой ({len(good)})", "", hdr, sep]
lines += [line(c) for c in sorted(good, key=lambda c: -tot("free", "B1", c)[0])] or ["| — |"]

# итоги пула v171c в том же окне: free — вычитание трёх монет (потолка нет, сделки независимы); B2 — потолок заново, из cells-v171c (portfolio-sim --drop)
cellsv = {(r["mode"], r["cell"]): r for r in csv.DictReader(open("docs/findings/cells-v171c-2026-10-09.csv", encoding="utf-8"))}
lines += ["", f"## Итого по клеткам — пул v171c (без {', '.join(sorted(d[:-4] for d in DROPPED))}), окно фев–сен", "",
          "free = итог по монетам без трёх (потолка нет — вычитание точно); B2 — потолок 3 применён заново (`cells-v171c-2026-10-09.csv`, portfolio-sim --drop); "
          "в таблицах (а)/(б) v171b-итоги по монетам, † — выпавшие монеты.", "", "| клетка | free v171b | free v171c | B2 v171c |", "|---|---|---|---|"]
for ce in CELLS:
    fb = sum(tot("free", ce, c)[0] for c in coins)
    fc = sum(tot("free", ce, c)[0] for c in coins if c not in DROPPED)
    b2 = cellsv.get(("B2", ce if ce != "B1" else "B1"))
    lines.append(f"| {ce} | {fb:+.0f} | {fc:+.0f} | {float(b2['usd_v171c']):+.0f} |" if b2 else f"| {ce} | {fb:+.0f} | {fc:+.0f} | — |")

# все монеты в минусе по каждой клетке, но не по всем — «большинство вариантов в минусе»
lines += ["", "## Число монет в минусе по итогу (из монет в счёте)", "", "| режим | клетка | в минусе | в плюсе |", "|---|---|---|---|"]
for mo, ce in keys:
    neg = sum(1 for c in coins if tot(mo, ce, c)[1] and tot(mo, ce, c)[0] < 0)
    pos = sum(1 for c in coins if tot(mo, ce, c)[1] and tot(mo, ce, c)[0] > 0)
    lines.append(f"| {mo} | {ce} | {neg} | {pos} |")

# (в) доля итога топ-5 монет
lines += ["", "## (в) Доля итога от топ-5 монет", "",
          "Два числа: топ-5 по $ / итог клетки и топ-5 / сумма плюсовых монет (итог бывает малым или отрицательным — первая доля тогда не читается).", "",
          "| режим | клетка | итог $ | топ-5 $ | топ-5 / итог | топ-5 / сумма плюсов | топ-5 монет |", "|---|---|---|---|---|---|---|"]
for mo, ce in keys:
    ps = sorted(((tot(mo, ce, c)[0], c) for c in coins), reverse=True)
    top = ps[:5]
    tt = sum(p for p, _ in ps)
    pos = sum(p for p, _ in ps if p > 0)
    t5 = sum(p for p, _ in top)
    r1 = f"{t5 / tt:.0%}" if tt > 0 else "—"
    lines.append(f"| {mo} | {ce} | {tt:+.0f} | {t5:+.0f} | {r1} | {(t5 / pos if pos else 0):.0%} | {', '.join(c.replace('USDT', '') for _, c in top)} |")

# месяцы: итог клетки и число монет в плюсе
lines += ["", "## По месяцам: $ / сделок (free)", "", "| месяц | " + " | ".join(CELLS) + " |", "|---|" + "---|" * len(CELLS)]
for m in MONTHS:
    cs = []
    for ce in CELLS:
        cs.append(f"{sum(v for k, v in usd.items() if k[0] == 'free' and k[1] == ce and k[3] == m):+.0f}/"
                  f"{sum(v for k, v in ntr.items() if k[0] == 'free' and k[1] == ce and k[3] == m)}")
    lines.append(f"| {m} | " + " | ".join(cs) + " |")
open("docs/findings/coins-breakdown-2026-10-09.md", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print("\n".join(lines[:40]))
