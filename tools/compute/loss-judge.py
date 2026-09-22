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
                 "цена правила на тех же сделках) — не больше двух атомов, чтобы не раздувать запрос:")
    shown: set[str] = set()
    for c in f["filters"]:
        if c["atom"] not in shown:
            if len(shown) >= 2:
                continue
            shown.add(c["atom"])
        lines.append(f"  {c['atom']}: порог {'≥' if c['side'] == '≥' else '≤'}{c['threshold']:.2f} → "
                     f"режет {c['cut_n']} сделок ({c['cut_neg']} минусовых), теряет прибыль "
                     f"${c['cut_pnl']:.2f}, P&L {c['kept_pnl']:.2f} из {total:.2f}")
    if not f["filters"]:
        lines.append("  нет предвходовых атомов с расхождением ≥ 0.25 IQR")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Судья по разбору убытков (TypeSafe, В-81)")
    ap.add_argument("--atoms", required=True)
    ap.add_argument("--neighbour", help="соседняя форма для проверки устойчивости")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    state = state_text(os.path.basename(a.atoms), facts(a.atoms))
    if a.neighbour:
        nf = facts(a.neighbour)
        state += "\n\nСоседняя форма (другие ttl/дедлайн/выход — сравнивать 1:1 нельзя, только знак):\n"
        state += state_text(os.path.basename(a.neighbour), nf)
    print(state)
    print("\n--- суждение ---")

    try:
        j = Judge()
    except MissingKey as e:
        print(f"loss-judge: {e} — только факты (выход 2)")
        return 2

    ans = j.ask(state, {
        "separates": Judge.noul(
            "Есть ли среди атомов, известных ДО входа, такой, что разделяет убыточные и прибыльные "
            "сделки устойчиво (а не как следствие хода цены после входа)?"),
        "best_candidate": Judge.choice(
            "Какой предвходовой кандидат в фильтр самый обоснованный по этим фактам?",
            {
                "pool_4h": "режим пула за 4 ч (рынок уже вырос — вход хуже)",
                "btc_4h": "ход BTC за 4 ч",
                "wall_age_min": "возраст стены",
                "strength_w20": "сила стены ×поток",
                "flow_1h_lots": "поток монеты за час",
                "arm_dist_bps": "расстояние взвода от стены",
                "none": "обоснованного кандидата нет — предвходовые атомы не разделяют",
            }),
        "overfit_1909": Judge.noul(
            "Есть ли риск, что любой вывод этого разбора подогнан под день 19.09 (единственный "
            "минусовой день) и развалится на новых днях?"),
        "next": Judge.choice(
            "Что делать дальше по этому разбору?",
            {
                "hold_more": "держать дольше (дедлайн/тейк дальше) — тейк не достигался ни разу",
                "exit_gone": "выход по снятию стены вместо 2 %-стопа",
                "stop1": "стоп 1 % вместо 2 %",
                "filter_pool": "фильтр по режиму пула (предвходовой)",
                "collect_days": "ничего не менять, копить дни падения и перепроверять",
                "replay_binlog": "пересчитать по бинлогам точный ход цены и ленте (A7/A4 точно)",
            }),
        "confidence": Judge.score(
            "Насколько уверенно эти данные (5 дней, 128 сделок, 54 минусовых) поддерживают фильтр?",
            ["случайность", "слабый намёк", "есть основание", "уверенно"]),
    })
    print(json.dumps(ans, ensure_ascii=False, indent=1))
    print("usage:", json.dumps(j.usage))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"facts": state, "answers": ans, "usage": j.usage}, f, ensure_ascii=False, indent=1)
        print(f"loss-judge: {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
