#!/usr/bin/env python3
"""Таблица семей флоров для H1b (handoff-2026-09-20.md): «семья × лучшая форма × n × точка/нижняя ×
монеты с ≥ 5 кругами».

Читает пару артефактов одной семьи: вердикт `study/bounce-verdict-<метка>.csv` (лучшая форма, точка и
нижняя граница, DSR, итог) и `b5/<метка>/forms.csv` (круги по монетам для этой формы). Одна команда —
одна строка таблицы; несколько `--family` — таблица целиком.

    python3 tools/compute/family-table.py \\
        --family "сила ≥100 %:study/bounce-verdict-v68lat-base-any-2026-09-19.csv:b5/v68lat-base-any" \\
        --family "возраст ≥45 мин:study/bounce-verdict-v70bal-a45-any-2026-09-19.csv:b5/v70bal-a45-any"

Формат `--family`: `<человеческое имя>:<вердикт.csv>:<каталог сетки>`. Монета «в плюсе» — сумма
`sum_net_bps` по форме > 0 (без интервалов: по монете их не построить, см. base68).
"""
import argparse
import collections
import csv
import os
import re
import sys


def parse_verdict(path):
    """Из шапки вердикта: лучшая форма, кругов, сигналов, точка/нижняя, DSR, итог."""
    out = {"form": "", "fills": "", "signals": "", "point": "", "lower": "", "dsr": "", "result": ""}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"лучшая форма \(по точке net_fill\): (\S+) — кругов (\d+) из (\d+) сигналов", text)
    if m:
        out["form"], out["fills"], out["signals"] = m.group(1), m.group(2), m.group(3)
    m = re.search(r"net_fill точка=([-\d.]+) нижняя=([-\d.]+) bps", text)
    if m:
        out["point"], out["lower"] = m.group(1), m.group(2)
    m = re.search(r"DSR=([\d.]+)", text)
    if m:
        out["dsr"] = m.group(1)
    m = re.search(r"ИТОГ: (.+)", text)
    if m:
        out["result"] = m.group(1).strip()
    return out


def load_forms(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def coin_stats(forms_path, form):
    """По монетам для формы: круги, сумма net, плюс/минус."""
    agg = collections.defaultdict(lambda: [0.0, 0.0])
    for r in load_forms(forms_path):
        if r["form"] != form:
            continue
        a = agg[r["symbol"]]
        a[0] += float(r["n_fills"] or 0)
        a[1] += float(r["sum_net_bps"] or 0)
    return agg


def forms_path_of(grid):
    """Третий аргумент `--family` — каталог сетки `b5/<метка>` или сам `forms.csv`."""
    return grid if grid.endswith(".csv") else os.path.join(grid, "forms.csv")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--family", action="append", required=True,
                   help="<имя>:<вердикт.csv>:<каталог сетки>, повторяемый")
    p.add_argument("--min-fills", type=int, default=5, help="порог «монета считается толстой»")
    p.add_argument("--out", help="куда продублировать таблицу (markdown)")
    a = p.parse_args()

    lines = ["| семья флоров | лучшая форма | кругов / сигналов | точка / нижняя, bps | DSR | итог | "
             "монет с ≥%d кругами (из них +) | лучшая | худшая |" % a.min_fills,
             "|---|---|---|---|---|---|---|---|---|"]
    for spec in a.family:
        parts = spec.split(":")
        if len(parts) != 3:
            raise SystemExit(f"--family ожидает <имя>:<вердикт>:<каталог>, получено {spec!r}")
        name, verdict, grid = parts
        v = parse_verdict(verdict)
        forms = forms_path_of(grid)
        agg = coin_stats(forms, v["form"]) if os.path.exists(forms) else {}
        # как в grid-coins: «толстые» монеты — те, у кого кругов ≥ порога; если такая одна, планка
        # опускается до 2 и 1, иначе лучшая=худшая (одна и та же монета) и строка не читается
        thick = agg
        for floor in (a.min_fills, 2, 1):
            sel = {s: x for s, x in agg.items() if x[0] >= floor}
            if len(sel) >= 2:
                thick = sel
                break
        pos = sum(1 for x in thick.values() if x[1] > 0)
        best = max(thick.items(), key=lambda kv: kv[1][1]) if thick else ("—", [0, 0])
        worst = min(thick.items(), key=lambda kv: kv[1][1]) if thick else ("—", [0, 0])
        lines.append(
            f"| {name} | `{v['form']}` | {v['fills']} / {v['signals']} | {v['point']} / {v['lower']} | "
            f"{v['dsr']} | {v['result']} | {len(thick)} ({pos}) | "
            f"{best[0][:-4]} {best[1][1]:+.0f} ({best[1][0]:.0f}) | "
            f"{worst[0][:-4]} {worst[1][1]:+.0f} ({worst[1][0]:.0f}) |")
    text = "\n".join(lines)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"out: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
