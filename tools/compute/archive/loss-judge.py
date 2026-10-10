#!/usr/bin/env python3
"""Судья по разбору убытков (В-81, план `loss-plan-2026-09-22.md` шаг 3): факты собирает код,
типизированное суждение даёт TypeSafe одним батч-вызовом.

    python3 loss-judge.py --atoms study/loss-atoms-5d.csv [--neighbour study/loss-atoms-5d-neighbour-gone50.csv]

Вопросы: разделяет ли хоть один ПРЕДвходовой атом убыточные и прибыльные; какой кандидат-фильтр
самый обоснованный; есть ли риск подгонки под 19.09; что делать дальше. Ответ — в док, §7.
Ключ — `TYPESAFE_API_KEY` (окружение; на счётной — /etc/alpha/typesafe.env).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "typesafe"))
from judge import Judge, MissingKey  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def load_report_module():
    spec = importlib.util.spec_from_file_location("loss_report", os.path.join(_HERE, "loss-report.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def facts(path: str) -> dict:
    lr = load_report_module()
    rows = lr.load(path)
    loss = [r for r in rows if (lr.fnum(r["pnl_usd"]) or 0) < 0]
    win = [r for r in rows if (lr.fnum(r["pnl_usd"]) or 0) >= 0]
    table = lr.atom_table(loss, win)
    filters = []
    for t in table:
        if t["atom"] not in lr.PRE_ENTRY or abs(t["diff_iqr"] or 0) < 0.25:
            continue
        for pct, key in ((0.25, "loss_q25"), (0.50, "loss_q50"), (0.75, "loss_q75")):
            if t[key] is not None:
                filters.append(lr.filter_cost(rows, t["atom"], t[key], t["diff"] > 0))
    return {"rows": rows, "loss": loss, "win": win, "table": table, "filters": filters, "lr": lr}


def state_text(name: str, f: dict) -> str:
    lr = f["lr"]
    total = sum(lr.fnum(r["pnl_usd"]) or 0 for r in f["rows"])
    lines = [
        f"Набор {name}: сделок {len(f['rows'])}, P&L ${total:.2f} на полный лот (В-83), "
        f"минусовых {len(f['loss'])} ({sum(lr.fnum(r['pnl_usd']) or 0 for r in f['loss']):.2f}), "
        f"плюсовых {len(f['win'])} (+{sum(lr.fnum(r['pnl_usd']) or 0 for r in f['win']):.2f}).",
        "Атомы (медиана убыточных / прибыльных, разность в единицах межквартильного размаха, "
        "когда известен признак):",
    ]
    for t in f["table"][:10]:
        lines.append(f"  {t['atom']} [{lr.when(t['atom'])}]: {t['loss_med']:.2f} / {t['win_med']:.2f}; "
                     f"разн/IQR {t['diff_iqr']:.2f}")
    days: dict[str, list] = {}
    for r in f["rows"]:
        days.setdefault(r["day"], []).append(r)
    lines.append("Дни: " + "; ".join(
        f"{d}: n={len(v)}, P&L {sum(lr.fnum(r['pnl_usd']) or 0 for r in v):.2f}" for d, v in sorted(days.items())))
    stops = [r for r in f["rows"] if r["reason"] == "stop"]
    lines.append("Стопы: " + "; ".join(
        f"{s['symbol']} {s['day']} {s['hour_utc']}ч: стена {s['wall_fate']} через {s['wall_fate_min']} мин, "
        f"цена тогда {s['mid_at_fate_bps']} bps, убыток {s['net_bps']} bps, ход против за удержание "
        f"{s['adverse_hold']} bps" for s in stops))
    dl = [r for r in f["rows"] if r["reason"] == "deadline" and (lr.fnum(r["pnl_usd"]) or 0) < 0]
    lines.append(f"Дедлайны с минусом: {len(dl)}, до тейка (+2 %) дошло "
                 f"{sum(1 for r in dl if lr.fnum(r['reached_take']) == 1)}; медиана хода в пользу "
                 f"{lr.q([lr.fnum(r['favour_hold']) for r in dl if lr.fnum(r['favour_hold']) is not None], 0.5)} bps")
    lines.append("Кандидаты в фильтр (предвходовые атомы; порог = квартиль убыточной группы; "
                 "цена правила на тех же сделках; «дни» — сколько суток правило улучшает P&L, кодом):")
    days_all = sorted({r["day"] for r in f["rows"]})
    for c in f["filters"]:
        better = 0
        for d in days_all:
            sub = [r for r in f["rows"] if r["day"] == d]
            cd = lr.filter_cost(sub, c["atom"], c["threshold"], c["side"] == "≥")
            better += 1 if cd["cut_pnl"] < 0 else 0
        c["days_better"] = better
        c["days_total"] = len(days_all)
        lines.append(f"  {c['atom']}: порог {'≥' if c['side'] == '≥' else '≤'}{c['threshold']:.2f} → "
                     f"режет {c['cut_n']} сделок ({c['cut_neg']} минусовых), теряет прибыль "
                     f"${c['cut_pnl']:.2f}, P&L {c['kept_pnl']:.2f} из {total:.2f}; "
                     f"улучшает P&L в {better} сутках из {len(days_all)}")
    if not f["filters"]:
        lines.append("  нет предвходовых атомов с расхождением ≥ 0.25 IQR")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Судья по разбору убытков (TypeSafe, В-81)")
    ap.add_argument("--atoms", required=True)
    ap.add_argument("--neighbour", help="соседняя форма для проверки устойчивости")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    fa = facts(a.atoms)
    state = state_text(os.path.basename(a.atoms), fa)
    if a.neighbour:
        nf = facts(a.neighbour)
        state += "\n\nСоседняя форма (другие ttl/дедлайн/выход — сравнивать 1:1 нельзя, только знак):\n"
        state += state_text(os.path.basename(a.neighbour), nf)
    print(state)

    try:
        j = Judge()
    except MissingKey as e:
        print(f"loss-judge: {e} — только факты (выход 2)")
        return 2

    # Аудит дизайна 22.09 §1: варианты выбора — только атомы, описанные в фактах (раньше в
    # запрос уходили два, а вариантов было семь — ответ был почти вынужден); «риск подгонки»
    # больше не спрашивается у модели (на вопрос «есть ли риск» она отвечает ~0.7 без данных) —
    # устойчивость по суткам считает код (`улучшает P&L в K сутках из N`). Ответы — мнение модели.
    cands = {c["atom"]: f"фильтр по {c['atom']}" for c in fa["filters"]}
    cands["none"] = "обоснованного предвходового кандидата нет"
    ans = j.ask(state, {
        "separates": Judge.noul(
            "Есть ли среди атомов, известных ДО входа, такой, что разделяет убыточные и прибыльные "
            "сделки устойчиво (а не как следствие хода цены после входа)?"),
        "best_candidate": Judge.choice(
            "Какой из перечисленных в фактах предвходовых кандидатов самый обоснованный?", cands),
        "next": Judge.choice(
            "Что делать дальше по этому разбору?",
            {
                "hold_more": "держать дольше (дедлайн/тейк дальше)",
                "exit_gone": "выход по снятию стены вместо 2 %-стопа",
                "stop1": "стоп 1 % вместо 2 %",
                "filter": "предвходовой фильтр из кандидатов",
                "collect_days": "ничего не менять, копить дни падения и перепроверять",
                "replay_binlog": "пересчитать по бинлогам точный ход цены и ленте",
            }),
        "support": Judge.score(
            "Насколько эти данные поддерживают введение фильтра?",
            ["случайность", "слабый намёк", "есть основание", "уверенно"]),
    })
    print(f"--- мнение модели {j.model_version} (не метрика; числа выше посчитаны кодом) ---")
    print(json.dumps(ans, ensure_ascii=False, indent=1))
    print("usage:", json.dumps(j.usage))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"facts": state, "answers": ans, "usage": j.usage}, f, ensure_ascii=False, indent=1)
        print(f"loss-judge: {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
