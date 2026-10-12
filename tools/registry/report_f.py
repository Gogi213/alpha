#!/usr/bin/env python3
"""TK-200 пакет F: помесячный свод сеток 9 мес. -> markdown. report_f.py <каталог csv> > отчёт.md. Единицы: $ (сумма закрытий), бп (markout/отклонение/sd), доли.
По каждой гипотезе — таблица «точка × месяц» (месяцы отдельно, без склейки); сводной строки «все 9 мес.» нет, она скрывает знак по месяцам."""
import csv, glob, os, sys

D = sys.argv[1]
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]


def load(prefix):
    out = []
    for f in sorted(glob.glob(os.path.join(D, prefix + "-2026-*.csv"))):
        with open(f, encoding="utf-8") as fh:
            out += list(csv.DictReader(fh))
    return out


def pivot(title, rows, key, val, fmt="{:+.1f}", note=""):
    cells, keys = {}, []
    for r in rows:
        k = key(r)
        if k not in keys:
            keys.append(k)
        cells[(k, r["month"] if "month" in r else r["period"])] = val(r)
    print(f"\n## {title}\n\n{note}\n\n{'точка' } | " + " | ".join(m[5:] for m in MONTHS))
    print("---|" + "---|" * len(MONTHS))
    for k in sorted(keys):
        print(f"{k} | " + " | ".join(fmt.format(cells[(k, m)]) if (k, m) in cells else "—" for m in MONTHS))


print("# TK-200 пакет F: сетки по месяцам (янв–сен 2026)\n\nМесяцы отдельно; $ — сумма закрытий круга в $ (не бп); бп — базисные пункты. Описательные Г-18/19/80/144/148 — без Холма (spec §0); шум для фильтров — вердикт исследования.")
r = load("g130_stop_round")
pivot("Г-130 (в) стоп-круг по свечам: Δ$ = сумма после − до (exit-sim = upper bound по исполнению: касание свечи = fill, без очереди и проскальзывания; трейл не моделируется — берётся его нижняя граница entry×1,01×0,99 тейкером)", r,
      lambda x: f"cap{x['cap']} {x['point']}", lambda x: float(x["sum_after"]) - float(x["sum_before"]), note="Δ$ по месяцу; заменено кругов — `rounds_replaced` в csv.")
r = load("g144_beta_hedge")
pivot("Г-144 хедж бета: среднее после − до, бп на круг", r, lambda x: x["point"], lambda x: float(x["mean_bps_after"]) - float(x["mean_bps_before"]), "{:+.2f}")
pivot("Г-144: sd после, бп (до — см. csv)", r, lambda x: x["point"], lambda x: float(x["sd_bps_after"]), "{:.1f}")
r = [x for x in load("g19_markout_rank") if int(x["n"])]
pivot("Г-19 markout по рангу: среднее, бп", r, lambda x: f"{x['point']} r{x['rank']}", lambda x: float(x["mean_bps"]), "{:+.1f}")
r = [x for x in load("g80_depth_excursion") if int(x["n"])]
pivot("Г-80 отклонение по бинам глубины: среднее, бп", r, lambda x: f"{x['point']} {x['bin']}", lambda x: float(x["excursion_mean_bps"]), "{:.0f}")
for pre, title in (("g124_profit_throttle-cap0", "Г-124 порог профита, cap0"), ("g124_profit_throttle-cap3", "Г-124, cap3"), ("g148_leverage-cap0", "Г-148 плечо, cap0"), ("g148_leverage-cap3", "Г-148, cap3")):
    r = load(pre)
    if r:
        pivot(title + ": Δ$ после − до", r, lambda x: x["point"], lambda x: float(x["sum_after"]) - float(x["sum_before"]))
