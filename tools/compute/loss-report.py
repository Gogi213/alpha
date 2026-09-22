#!/usr/bin/env python3
"""Сравнения по атомам: убыточные против прибыльных, 19.09 против остальных, стопы, дедлайны,
кандидаты в фильтр с ценой (сколько убытка режет, сколько прибыли теряет) — план
`docs/plan/loss-plan-2026-09-22.md`, шаг 2.

    python3 loss-report.py --atoms study/loss-atoms-5d.csv [--json study/loss-report-5d.json]

Числа только считаются (факты), интерпретация — в доке и у судьи TypeSafe. Порогов не назначаем:
каждому атому печатаются три порога — квартили убыточной группы (q25/q50/q75), чтобы видеть цену
фильтра, а не выбирать его здесь. Правило проекта: «фильтр найденным не считается», это кандидаты.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import Counter, defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

NUMERIC = [
    "net_bps", "pnl_usd", "hold_min", "fill_frac", "legs_filled",
    "btc_1h", "btc_4h", "pool_1h", "pool_4h", "eth_4h",
    "wall_age_min", "wall_size_lots", "wall_size_usd", "arm_dist_bps", "strength_w20",
    "flow_1h_lots", "repeat_before", "wall_fate_min", "size_at_exit_ratio",
    "adverse_5m", "adverse_15m", "adverse_60m", "favour_5m", "favour_15m", "favour_60m",
    "adverse_hold", "favour_hold", "reached_take", "mid_at_deadline_bps", "mid_at_fate_bps",
    "entry_vs_wall_bps",
    "flow_proxy_traded_during", "flow_proxy_frontrun_lots", "flow_proxy_swept_lots", "hour_utc",
]
# атомы, которые годятся в фильтр: известны ДО входа. Послевходовые (ход цены, судьба стены) —
# следствие исхода, фильтром быть не могут, но объясняют, как теряли.
PRE_ENTRY = [
    "btc_1h", "btc_4h", "pool_1h", "pool_4h", "eth_4h",
    "wall_age_min", "wall_size_lots", "wall_size_usd", "arm_dist_bps", "strength_w20",
    "flow_1h_lots", "repeat_before", "entry_vs_wall_bps", "fill_frac", "hour_utc",
    "flow_proxy_traded_during", "flow_proxy_frontrun_lots", "flow_proxy_swept_lots",
]
POST_ENTRY = [
    "wall_fate_min", "size_at_exit_ratio", "adverse_5m", "adverse_15m", "adverse_60m",
    "favour_5m", "favour_15m", "favour_60m", "adverse_hold", "favour_hold",
    "reached_take", "mid_at_deadline_bps", "mid_at_fate_bps", "hold_min", "legs_filled",
]
OUTCOME = ["net_bps", "pnl_usd"]


def when(atom: str) -> str:
    if atom in PRE_ENTRY:
        return "до входа"
    if atom in OUTCOME:
        return "исход"
    return "после входа"


def fnum(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def col(rows: list[dict], name: str) -> list[float]:
    return [v for v in (fnum(r.get(name)) for r in rows) if v is not None]


def q(vals: list[float], p: float) -> float | None:
    """Квартиль линейной интерполяцией (как numpy по умолчанию)."""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = p * (len(s) - 1)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def fmt(v: float | None, w: int = 8, digits: int = 2) -> str:
    if v is None:
        return "—".rjust(w)
    if isinstance(v, float) and (abs(v) < 0.005 and v != 0):
        return f"{v:.4f}".rjust(w)
    return f"{v:.{digits}f}".rjust(w)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def atom_table(loss: list[dict], win: list[dict]) -> list[dict]:
    """Медианы и квартили по каждому атому + разность в единицах межквартильного размаха."""
    out = []
    for a in NUMERIC:
        lv, wv = col(loss, a), col(win, a)
        if len(lv) < 3 or len(wv) < 3:
            continue
        ml, mw = st.median(lv), st.median(wv)
        iqr = (q(lv + wv, 0.75) or 0) - (q(lv + wv, 0.25) or 0)
        out.append({
            "atom": a, "n_loss": len(lv), "n_win": len(wv),
            "loss_med": ml, "win_med": mw, "diff": ml - mw,
            "diff_iqr": (ml - mw) / iqr if iqr else None,
            "loss_q25": q(lv, 0.25), "loss_q50": q(lv, 0.5), "loss_q75": q(lv, 0.75),
            "win_q25": q(wv, 0.25), "win_q75": q(wv, 0.75),
        })
    out.sort(key=lambda x: -abs(x["diff_iqr"] or 0))
    return out


def filter_cost(rows: list[dict], atom: str, thr: float, high_is_bad: bool) -> dict:
    """Что даёт правило «пропускать сделки с атомом хуже порога» на тех же 128 сделках."""
    def keep(r: dict) -> bool:
        v = fnum(r.get(atom))
        if v is None:
            return True  # пустая ячейка — не повод выбрасывать сделку
        return v < thr if high_is_bad else v > thr

    kept = [r for r in rows if keep(r)]
    cut = [r for r in rows if not keep(r)]
    return {
        "atom": atom, "threshold": thr, "side": "≥" if high_is_bad else "≤",
        "cut_n": len(cut), "cut_pnl": sum(fnum(r["pnl_usd"]) or 0 for r in cut),
        "kept_n": len(kept), "kept_pnl": sum(fnum(r["pnl_usd"]) or 0 for r in kept),
        "kept_neg": sum(1 for r in kept if (fnum(r["pnl_usd"]) or 0) < 0),
        "cut_neg": sum(1 for r in cut if (fnum(r["pnl_usd"]) or 0) < 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сравнения по атомам сделок (loss-plan шаг 2)")
    ap.add_argument("--atoms", required=True)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)

    rows = load(a.atoms)
    loss = [r for r in rows if (fnum(r["pnl_usd"]) or 0) < 0]
    win = [r for r in rows if (fnum(r["pnl_usd"]) or 0) >= 0]
    total = sum(fnum(r["pnl_usd"]) or 0 for r in rows)
    report: dict = {"n": len(rows), "n_loss": len(loss), "n_win": len(win), "pnl_usd": total}

    print(f"атомов-строк {len(rows)} · P&L ${total:.2f} (полный лот, В-83) · "
          f"минусовых {len(loss)} ({sum(fnum(r['pnl_usd']) or 0 for r in loss):.2f}) · "
          f"плюсовых {len(win)} (+{sum(fnum(r['pnl_usd']) or 0 for r in win):.2f})")

    section("1. Убыточные против прибыльных (медианы; разность в единицах межквартильного размаха)")
    table = atom_table(loss, win)
    report["atoms"] = table
    print(f"{'атом':<26}{'когда':<12}{'убыт':>9}{'приб':>9}{'разн':>9}{'разн/IQR':>10}"
          f"{'убыт q25':>10}{'убыт q75':>10}")
    for t in table[:a.top]:
        print(f"{t['atom']:<26}{when(t['atom']):<12}{fmt(t['loss_med'])}{fmt(t['win_med'])}"
              f"{fmt(t['diff'])}{fmt(t['diff_iqr'])}{fmt(t['loss_q25'])}{fmt(t['loss_q75'])}")

    section("2. Дни: 19.09 против остальных")
    days = defaultdict(list)
    for r in rows:
        days[r["day"]].append(r)
    print(f"{'день':<12}{'n':>4}{'P&L':>9}{'btc_4h':>9}{'pool_4h':>9}{'adverse_60m':>12}"
          f"{'favour_60m':>11}{'возраст':>9}")
    for day in sorted(days):
        d = days[day]
        pnl = sum(fnum(r["pnl_usd"]) or 0 for r in d)
        med = lambda c: st.median(col(d, c)) if col(d, c) else None  # noqa: E731
        med_adv, med_fav = med("adverse_60m"), med("favour_60m")
        print(f"{day:<12}{len(d):>4}{pnl:>9.2f}{fmt(med('btc_4h'))}{fmt(med('pool_4h'))}"
              f"{fmt(med_adv, 12)}{fmt(med_fav, 11)}{fmt(med('wall_age_min'))}")
    report["days"] = {d: {"n": len(v), "pnl_usd": sum(fnum(r["pnl_usd"]) or 0 for r in v)}
                      for d, v in sorted(days.items())}

    # судьба стены по исходу
    section("2б. Судьба стены и исход")
    for name, grp in (("минусовые", loss), ("плюсовые", win)):
        c = Counter(r["wall_fate"] or "?" for r in grp)
        print(f"  {name:<10}" + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    report["fate"] = {name: dict(Counter(r["wall_fate"] or "?" for r in grp))
                      for name, grp in (("loss", loss), ("win", win))}

    section("3. Стопы (рассказ на строку)")
    stops = [r for r in rows if r["reason"] == "stop"]
    report["stops"] = stops
    for s in stops:
        print(f"  {s['symbol']} {s['day']} {s['hour_utc']}:00 · вход {s['entry_px']} выход {s['exit_px']} · "
              f"hold {s['hold_min']} мин · стена {s['wall_fate']} через {s['wall_fate_min']} мин · "
              f"BTC4h {s['btc_4h']} пул4h {s['pool_4h']}")
        print(f"      против 5/15/60: {s['adverse_5m']}/{s['adverse_15m']}/{s['adverse_60m']} bps · "
              f"в пользу 5/15/60: {s['favour_5m']}/{s['favour_15m']}/{s['favour_60m']} · "
              f"тейк на пути: {s['reached_take']} · возраст стены {s['wall_age_min']} мин · "
              f"сила {s['strength_w20']} % · поток {s['flow_1h_lots']} · касаний до {s['repeat_before']}")
        print(f"      ход за всё удержание: против {s['adverse_hold']} / в пользу {s['favour_hold']} bps · "
              f"цена в момент события стены {s['mid_at_fate_bps']} bps (выход по снятию стены дал бы "
              f"столько вместо {s['net_bps']}) · к дедлайну {s['mid_at_deadline_bps']} bps")

    section("4. Дедлайны с минусом: дошла ли цена до тейка")
    dl_neg = [r for r in rows if r["reason"] == "deadline" and (fnum(r["pnl_usd"]) or 0) < 0]
    dl_pos = [r for r in rows if r["reason"] == "deadline" and (fnum(r["pnl_usd"]) or 0) >= 0]
    for name, grp in (("минусовые", dl_neg), ("плюсовые", dl_pos)):
        rt = [fnum(r["reached_take"]) for r in grp if fnum(r["reached_take"]) is not None]
        md = col(grp, "mid_at_deadline_bps")
        share = 100 * sum(1 for v in rt if v == 1) / len(rt) if rt else 0.0
        mid_txt = f"{st.median(md):.1f} bps" if md else "—"
        print(f"  {name}: n={len(grp)} · тейк был на пути у {sum(1 for v in rt if v == 1)}/{len(rt)} "
              f"({share:.0f} %) · медиана у дедлайна {mid_txt}")
    report["deadline_neg"] = {"n": len(dl_neg),
                              "reached_take": sum(1 for v in (fnum(r["reached_take"]) for r in dl_neg) if v == 1)}

    section("5. Кандидаты в фильтр: только предвходовые атомы, порог = квартиль убыточной группы")
    cands = [t for t in table if t["atom"] in PRE_ENTRY and abs(t["diff_iqr"] or 0) >= 0.25]
    print("(правило: пропускать сделку, если атом хуже порога; пустая ячейка не выбрасывает)")
    print("(послевходовых в списке нет: ход цены и судьба стены — следствие, а не признак входа)")
    print(f"{'атом':<24}{'порог':>10}{'режет':>7}{'минусов':>8}{'прибыль режет':>14}"
          f"{'P&L':>9}{'было':>9}{'осталось минусов':>18}")
    report["filters"] = []
    for t in cands:
        high_is_bad = t["diff"] > 0
        for pct, key in ((0.25, "loss_q25"), (0.50, "loss_q50"), (0.75, "loss_q75")):
            thr = t[key]
            if thr is None:
                continue
            res = filter_cost(rows, t["atom"], thr, high_is_bad)
            res["pct"] = pct
            res["diff_iqr"] = t["diff_iqr"]
            report["filters"].append(res)
            print(f"{t['atom']:<24}{fmt(thr, 10)}{res['cut_n']:>7}{res['cut_neg']:>8}"
                  f"{res['cut_pnl']:>14.2f}{res['kept_pnl']:>9.2f}{total:>9.2f}{res['kept_neg']:>18}")
    if not cands:
        print("  предвходовых атомов с расхождением ≥ 0.25 IQR нет — кандидатов в фильтр нет")

    section("6. Что дали бы правила выхода (грубая оценка по тем же 128 сделкам)")
    print("(цена выхода — середина из mids1m в минуту события стены, без комиссий; тейк не моделируем;")
    print(" порядок «стена снята» и «стоп 1 %» внутри одной минуты не восстановлен — стена важнее)")
    variants: dict[str, list[float]] = {}

    def gone_net(r: dict) -> float:
        orig = fnum(r["net_bps"]) or 0.0
        fate, fm = r.get("wall_fate"), fnum(r.get("wall_fate_min"))
        mid = fnum(r.get("mid_at_fate_bps"))
        hold = fnum(r.get("hold_min")) or 0.0
        if fate in ("сняли", "съели") and mid is not None and fm is not None and fm < hold:
            return mid
        return orig

    def stop1_net(r: dict) -> float:
        orig = fnum(r["net_bps"]) or 0.0
        adv = fnum(r.get("adverse_hold"))
        return -100.0 if adv is not None and adv >= 100.0 else orig

    def both_net(r: dict) -> float:
        fate, fm = r.get("wall_fate"), fnum(r.get("wall_fate_min"))
        if fate in ("сняли", "съели") and fm is not None and fm <= 60:
            return gone_net(r)
        return stop1_net(r)

    for name, fn in (("как есть", lambda r: fnum(r["net_bps"]) or 0.0),
                     ("выход по снятию стены", gone_net),
                     ("стоп 1 % вместо 2 %", stop1_net),
                     ("снятие стены + стоп 1 %", both_net)):
        vals = [fn(r) for r in rows]
        variants[name] = vals
        pnl = sum(vals) / 1e4 * 1000.0
        neg = sum(1 for v in vals if v < 0)
        changed = sum(1 for r, v in zip(rows, vals) if abs(v - (fnum(r["net_bps"]) or 0.0)) > 1e-9)
        print(f"  {name:<24} P&L ${pnl:>8.2f} · минусовых {neg:>3} · изменено сделок {changed:>3}")
    report["exit_variants"] = {k: round(sum(v) / 1e4 * 1000.0, 2) for k, v in variants.items()}

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"\nloss-report: {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
