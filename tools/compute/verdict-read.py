#!/usr/bin/env python3
"""Чтение вердиктов сетки (В-81 п. 2, TypeSafe в мета-контуре): по каждому `bounce-verdict-*.csv`
— факты кодом (лучшие формы по нижней границе и по точке, доля форм с n ≥ 100, доли выходов),
суждение моделью: живые формы для короткого списка, мёртвые оси, что читать человеку. Итог —
таблица на набор + короткий список форм, вместо чтения 8 × 300 строк.

    python3 bin/verdict-read.py study/bounce-verdict-f10fix-*.csv [--top 5] [--json]

Ключ — `TYPESAFE_API_KEY` (на счётной — /etc/alpha/typesafe.env). Без ключа — выход 2 и только
факты (таблица без суждения).
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

GATE_N = 100  # ворота вердикта (В-58/В-60): n ≥ 100 кругов


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
    by_lower = sorted(gated, key=lambda r: r["lower"], reverse=True)[:top]
    by_point = sorted(gated, key=lambda r: r["point"], reverse=True)[:top]
    pos_lower = [r for r in gated if r["lower"] > 0]
    # Оси: средняя точка по значению каждого поля имени формы (вход-стоп-тейк-дедлайн-ttl-выход).
    axes: dict[str, dict[str, list[float]]] = {}
    for r in gated:
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
            f"  {r['form']}: сделок {r['n']} из {r['n_signals']} сигналов, точка {r['point']:+.2f}, "
            f"нижняя {r['lower']:+.2f}, на сделку {r['per_fill']:+.1f} bps; выходы: стоп {r['share_stop']:.0%}, "
            f"тейк {r['share_take']:.0%}, дедлайн {r['share_timeout']:.0%}, съели {r['share_eaten_by_trades']:.0%}, "
            f"сняли {r['share_wall_gone']:.0%}"
        )

    text = "\n".join(
        [
            f"Набор: {head.get('grid', head['path'])}; форм {len(rows)}, символов {head.get('symbols', '?')}, суток {head.get('days', '?')}.",
            f"Форм с n ≥ {GATE_N} сделок: {len(gated)} из {len(rows)}; форм с нижней границей > 0: {len(pos_lower)}.",
            f"DSR лучшей формы: {head.get('dsr_best')}.",
            "Лучшие по нижней границе (среди n ≥ 100):",
            *[row_line(r) for r in by_lower],
            "Лучшие по точке (среди n ≥ 100):",
            *[row_line(r) for r in by_point],
            "Средняя точка по значениям осей (bps, среди n ≥ 100):",
            *axis_lines,
        ]
    )
    summary = {
        "grid": head.get("grid", head["path"]),
        "forms": len(rows),
        "gated": len(gated),
        "pos_lower": len(pos_lower),
        "best_lower": by_lower[0] if by_lower else None,
        "best_point": by_point[0] if by_point else None,
    }
    return text, summary


CONTEXT = (
    "Проект alpha ищет альфу в отскоке от крупных плотностей стакана. Вердикт сетки: форма — вход "
    "(лестница), стоп, тейк, дедлайн, срок жизни входа (ttl), выход (none/eat/gone). Ворота: n ≥ 100 "
    "сделок и нижняя граница net_fill > 0 (бутстрэп по суткам). «Живая» форма — та, что имеет смысл "
    "оставить в коротком списке для следующих ночей: n ≥ 100, точка заметно > 0, нижняя близка к нулю "
    "или выше, доля стопов не доминирует. «Мёртвая ось» — значение оси, чьи формы стабильно хуже "
    "остальных. Комиссии круга ~4.4 bps уже вычтены (net)."
)


def judge_set(j: Judge, text: str) -> dict:
    return j.ask(
        CONTEXT + "\n\nФакты вердикта:\n" + text,
        {
            "status": Judge.choice(
                "Как читать этот набор?",
                {
                    "promising": "есть формы с n ≥ 100 и точкой заметно выше нуля при нижней границе около нуля — держать в коротком списке, копить сутки",
                    "flat": "формы вокруг нуля (точка < ~1 bps) — стратегия на этом наборе не зарабатывает",
                    "negative": "точки отрицательны у большинства форм — набор вредит",
                    "insufficient": "мало форм с n ≥ 100 — читать нельзя, нужны сутки",
                },
            ),
            "keep_axis_hint": Judge.choice(
                "Какая ось сильнее всего разделяет формы по точке (судя по средним по осям)?",
                {"entry": "лестница входа", "stop": "стоп", "take": "тейк", "deadline": "дедлайн", "ttl": "срок жизни входа", "exit": "форма выхода", "none": "различия невелики"},
            ),
            "human_needed": Judge.noul("Есть ли в фактах что-то неожиданное, что человеку стоит посмотреть глазами (аномальная доля выходов, странный разрыв точка/нижняя, форма с n ≫ остальных)?"),
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
    shortlist: list[tuple[str, dict]] = []
    for p in a.paths:
        head, rows = read_verdict(p)
        text, summ = facts(head, rows, a.top)
        ans = None
        if j is not None:
            try:
                ans = judge_set(j, text)
            except RuntimeError as e:
                print(f"verdict-read: {e}")
        status = ans["status"]["choice"] if ans else "?"
        conf = ans["status"]["confidence"] if ans else 0.0
        axis = ans["keep_axis_hint"]["choice"] if ans else "?"
        human = ans["human_needed"]["noul"] if ans else 0.0
        b = summ["best_lower"]
        best = f"{b['form']} (n={b['n']}, точка {b['point']:+.2f}, нижняя {b['lower']:+.2f})" if b else "—"
        print(
            f"{os.path.basename(p)}: {status} ({conf:.2f}); форм n≥100: {summ['gated']}/{summ['forms']}, "
            f"нижняя>0: {summ['pos_lower']}; лучшая по нижней: {best}; ось: {axis}; глазами: {human:.2f}"
        )
        if status == "promising" and b:
            shortlist.append((summ["grid"], b))
        out.append({"path": p, "facts": text, "summary": summ, "answers": ans})
    if shortlist:
        print("Короткий список (лучшая по нижней границе в перспективных наборах):")
        for g, b in shortlist:
            print(f"  {g}: {b['form']}")
    if j is not None:
        print(f"verdict-read: TypeSafe {j.usage['requests']} запросов, {j.usage['input_tokens']}/{j.usage['output_tokens']} токенов")
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if j is not None else 2


if __name__ == "__main__":
    sys.exit(main())
