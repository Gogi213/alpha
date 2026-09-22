#!/usr/bin/env python3
"""Чтение вердиктов сетки (В-81 п. 2; граница кода и модели — аудит дизайна 22.09 §1): по каждому
`bounce-verdict-*.csv` — итог вердикта (`ИТОГ:` из шапки), лучшие формы по нижней границе и по
точке, доля форм с n ≥ 100, ось с наибольшим разбросом средних точек — всё кодом. Модель (Jev) —
только мнение «есть ли что посмотреть глазами», с версией модели. Короткий список форм модель
больше не составляет: раньше её рамка переименовывала машинный «красный» в «перспективный».

    python3 bin/verdict-read.py study/bounce-verdict-f10fix-*.csv [--top 5] [--json]

Ключ — `TYPESAFE_API_KEY`; без ключа печатаются только факты, выход 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

GATE_N = 100  # ворота «торгуем» (В-58/В-60); в РАЗБОРЕ гипотез порога нет (владелец 22.09) — ранжируем по точке с интервалом


def read_verdict(path: str) -> tuple[dict, list[dict]]:
    head: dict = {"path": path}
    rows: list[dict] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        lines = f.read().splitlines()
    body = [l for l in lines if not l.startswith("#")]
    for l in lines:
        if l.startswith("# lob bounce-verdict"):
            m = re.search(r"grid=(\S+) форм (\d+) символов (\d+) суток (\d+)", l)
            if m:
                head.update(grid=m.group(1), forms=int(m.group(2)), symbols=int(m.group(3)), days=int(m.group(4)))
        if l.startswith("# лучшая форма"):
            m = re.search(r"DSR=([-0-9.]+)", l)
            head["dsr_best"] = float(m.group(1)) if m else None
        if "ИТОГ:" in l:
            head["verdict"] = l.split("ИТОГ:", 1)[1].strip()
    for r in csv.DictReader(body):
        try:
            rows.append(
                {
                    "form": r["form"],
                    "n_signals": int(r["n_signals"]),
                    "n": int(r["n_fills"]),
                    "point": float(r["net_fill_point_bps"]),
                    "lower": float(r["net_fill_lower_bps"]),
                    "per_fill": float(r["net_per_fill_bps"]),
                    "sharpe": float(r["sharpe"]),
                    "share_stop": float(r.get("share_stop", 0) or 0),
                    "share_take": float(r.get("share_take", 0) or 0),
                    "share_timeout": float(r.get("share_timeout", 0) or 0),
                    "share_eaten_by_trades": float(r.get("share_eaten_by_trades", 0) or 0),
                    "share_wall_gone": float(r.get("share_wall_gone", 0) or 0),
                }
            )
        except (KeyError, ValueError):
            continue
    return head, rows


def facts(head: dict, rows: list[dict], top: int) -> tuple[str, dict]:
    gated = [r for r in rows if r["n"] >= GATE_N]
    traded = [r for r in rows if r["n"] > 0]
    # Разбор — без порога по числу сделок (владелец 22.09: «порог 100 вредоносен»
    # для чтения): ранжируем все торговавшие формы по точке с интервалом; n ≥ 100
    # остаётся только меткой «прошла бы ворота».
    by_lower = sorted(traded, key=lambda r: r["lower"], reverse=True)[:top]
    by_point = sorted(traded, key=lambda r: r["point"], reverse=True)[:top]
    pos_lower = [r for r in traded if r["lower"] > 0]
    # Оси: средняя точка по значению каждого поля имени формы (вход-стоп-тейк-дедлайн-ttl-выход).
    axes: dict[str, dict[str, list[float]]] = {}
    for r in traded:
        parts = r["form"].split("-")
        names = ["entry", "stop", "take", "deadline", "ttl", "exit"][: len(parts)]
        for k, v in zip(names, parts):
            axes.setdefault(k, {}).setdefault(v, []).append(r["point"])
    axis_lines = []
    for k, vals in axes.items():
        cells = ", ".join(f"{v}: {sum(p) / len(p):+.2f} (n={len(p)})" for v, p in sorted(vals.items()))
        axis_lines.append(f"  {k}: {cells}")

    def row_line(r: dict) -> str:
        return (
            f"  {r['form']}: сделок {r['n']} из {r['n_signals']} сигналов{' (ворота n≥100: да)' if r['n'] >= GATE_N else ''}, точка {r['point']:+.2f}, "
            f"нижняя {r['lower']:+.2f}, на сделку {r['per_fill']:+.1f} bps; выходы: стоп {r['share_stop']:.0%}, "
            f"тейк {r['share_take']:.0%}, дедлайн {r['share_timeout']:.0%}, съели {r['share_eaten_by_trades']:.0%}, "
            f"сняли {r['share_wall_gone']:.0%}"
        )

    text = "\n".join(
        [
            f"Набор: {head.get('grid', head['path'])}; форм {len(rows)}, символов {head.get('symbols', '?')}, суток {head.get('days', '?')}.",
            f"Форм со сделками: {len(traded)} из {len(rows)}; из них прошли бы ворота n ≥ {GATE_N}: {len(gated)}; форм с нижней границей > 0: {len(pos_lower)}.",
            f"DSR лучшей формы: {head.get('dsr_best')}.",
            "Лучшие по нижней границе (все торговавшие формы):",
            *[row_line(r) for r in by_lower],
            "Лучшие по точке (все торговавшие формы):",
            *[row_line(r) for r in by_point],
            "Средняя точка по значениям осей (bps, все торговавшие формы):",
            *axis_lines,
        ]
    )
    # Ось с наибольшим разбросом средних точек по её значениям — кодом (раньше спрашивали модель).
    spread = {
        k: max(sum(p) / len(p) for p in vals.values()) - min(sum(p) / len(p) for p in vals.values())
        for k, vals in axes.items()
        if len(vals) > 1
    }
    summary = {
        "grid": head.get("grid", head["path"]),
        "verdict": head.get("verdict", "?"),
        "forms": len(rows),
        "gated": len(gated),
        "pos_lower": len(pos_lower),
        "best_lower": by_lower[0] if by_lower else None,
        "best_point": by_point[0] if by_point else None,
        "axis": max(spread, key=spread.get) if spread else "none",
        "axis_spread_bps": round(max(spread.values()), 2) if spread else 0.0,
    }
    return text, summary


CONTEXT = (
    "Проект alpha: бэктест отскока от крупных плотностей стакана. Вердикт сетки: форма — вход "
    "(лестница), стоп, тейк, дедлайн, срок жизни входа (ttl), выход (none/eat/gone). Итог вердикта, "
    "лучшие формы и ось с наибольшим разбросом уже посчитаны кодом. Комиссии круга уже вычтены (net)."
)


def judge_set(j: Judge, text: str) -> dict:
    # Аудит дизайна 22.09 §1: прежняя рамка вкладывала в модель тезис «60 сделок и +12 bps —
    # сильный сигнал» и определяла вариант «перспективный» как «нижняя граница объяснима малой
    # выборкой» — машинный «красный» переименовывался в «перспективный». Статус и ось теперь
    # решает код; модель спрашивается только о том, что стоит посмотреть глазами.
    return j.ask(
        CONTEXT + "\n\nФакты вердикта:\n" + text,
        {
            "human_needed": Judge.noul(
                "Есть ли в фактах что-то неожиданное, что человеку стоит посмотреть глазами "
                "(аномальная доля одной причины выхода, странный разрыв точки и нижней границы, "
                "форма с числом сделок намного больше остальных)?"
            ),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        j: Judge | None = Judge()
    except MissingKey as e:
        print(f"verdict-read: {e} — только факты")
        j = None
    out = []
    for p in a.paths:
        head, rows = read_verdict(p)
        text, summ = facts(head, rows, a.top)
        ans = None
        if j is not None:
            try:
                ans = judge_set(j, text)
            except RuntimeError as e:
                print(f"verdict-read: {e}")
        b = summ["best_lower"]
        best = f"{b['form']} (n={b['n']}, точка {b['point']:+.2f}, нижняя {b['lower']:+.2f})" if b else "—"
        opinion = (
            f"; мнение модели {j.model_version}: посмотреть глазами p={ans['human_needed']['noul']:.2f}"
            if ans and j is not None else ""
        )
        # Первым — машинный итог вердикта (аудит 22.09 §4 С6: статус перед числами).
        print(
            f"{os.path.basename(p)}: ИТОГ {summ['verdict']}; форм n≥100: {summ['gated']}/{summ['forms']}, "
            f"нижняя>0: {summ['pos_lower']}; лучшая по нижней: {best}; ось разброса: {summ['axis']} "
            f"({summ['axis_spread_bps']:+.2f} bps){opinion}"
        )
        out.append({"path": p, "facts": text, "summary": summ, "answers": ans})
    if j is not None:
        print(
            f"verdict-read: TypeSafe {j.model_version}, {j.usage['requests']} запросов, "
            f"{j.usage['input_tokens']}/{j.usage['output_tokens']} токенов"
        )
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
