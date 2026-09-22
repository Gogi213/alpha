#!/usr/bin/env python3
"""Разбивка результата одной формы сетки (владелец 22.09): тотал, по дням, по неделям, по монетам,
по стороне, по типу сделки (причина выхода; ноги лестницы) — P&L, winrate, просадка, Шарп, объём позиции.

    python3 bin/breakdown.py --grid-dir b5/f10fix-D20-5d/a45-bid --form <имя формы> [--order-usd 1000] [--top 15] [--json out.json]

Деньги: P&L сделки = net_bps / 10 000 × order_usd — **на полный плановый лот** (В-83, владелец 22.09: сетка
лимиток забирает весь размер в позицию, частичное исполнение в деньгах не различается); `--by-fill` —
прежний счёт на исполненную долю (fill_frac, как в equity-report.py). «Объём позиции» — план order_usd и
факт при лоте пула (qty × entry_px); исполненная доля печатается справочно.
Просадка — максимальная по накопленному P&L в порядке времени входа.

Аудит дизайна 22.09 (§0 п. 4–6, В-85 п. 7): прежний «Шарп по сделкам» (mean/std × √n) — это t-статистика
при допущении, что сделки независимы, а не Шарп; колонка и строка теперь так и называются. Первой строкой
печатается машинный итог вердикта (`--verdict`), рядом с деньгами — контроль «рост рынка» (`--mids`,
`placebo.py`), концентрация по монетам (доля топ-4) и капитал под пик одновременных позиций.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def load_rounds(path: str, form: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        body = [l for l in f if not l.startswith("#")]
    for r in csv.DictReader(body):
        if r["form"] != form:
            continue
        rows.append(
            {
                "symbol": r["symbol"],
                "day": r["day_utc"],
                "t0_ns": int(r["t0_ns"]),
                "dir": int(r["dir"]),
                "entry_px": float(r["entry_px"]),
                "exit_px": float(r["exit_px"]),
                "qty": float(r["qty"]),
                "net_bps": float(r["net_bps"]),
                "reason": r["reason"],
                "exit_ns": int(r["exit_ns"]) if r.get("exit_ns") else 0,
                "fill_frac": float(r.get("fill_frac") or 1.0),
                "legs_filled": int(r.get("legs_filled") or 1),
            }
        )
    rows.sort(key=lambda x: x["t0_ns"])
    return rows


BY_FILL = False


def trade_pnl(r: dict, order_usd: float) -> float:
    return r["net_bps"] / 1e4 * order_usd * (r["fill_frac"] if BY_FILL else 1.0)


def stats(rows: list[dict], order_usd: float) -> dict:
    if not rows:
        return {"n": 0}
    pnl = [trade_pnl(r, order_usd) for r in rows]
    wins = sum(1 for p in pnl if p > 0)
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for p in pnl:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    n = len(pnl)
    mean = sum(pnl) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in pnl) / (n - 1)) if n > 1 else 0.0
    sharpe_trade = mean / sd * math.sqrt(n) if sd > 0 else 0.0
    by_day = defaultdict(float)
    for r, p in zip(rows, pnl):
        by_day[r["day"]] += p
    d = list(by_day.values())
    dmean = sum(d) / len(d)
    dsd = math.sqrt(sum((x - dmean) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0.0
    sharpe_day = dmean / dsd * math.sqrt(365) if dsd > 0 else 0.0
    pos_plan = sum(order_usd * (r["fill_frac"] if BY_FILL else 1.0) for r in rows) / n
    pos_fact = sum(r["qty"] * r["entry_px"] * r["fill_frac"] for r in rows) / n
    hold_min = [max(0, (r["exit_ns"] - r["t0_ns"]) / 6e10) for r in rows if r["exit_ns"]]
    return {
        "n": n,
        "pnl_usd": sum(pnl),
        "pnl_bps_mean": sum(r["net_bps"] for r in rows) / n,
        "winrate": wins / n,
        "avg_win": (sum(p for p in pnl if p > 0) / wins) if wins else 0.0,
        "avg_loss": (sum(p for p in pnl if p <= 0) / (n - wins)) if n - wins else 0.0,
        "max_dd_usd": dd,
        "sharpe_trade": sharpe_trade,
        "sharpe_day": sharpe_day,
        "days": len(by_day),
        "pos_plan_usd": pos_plan,
        "pos_fact_usd": pos_fact,
        "fill_frac": sum(r["fill_frac"] for r in rows) / n,
        "hold_min_med": sorted(hold_min)[len(hold_min) // 2] if hold_min else 0.0,
    }


def fmt_row(name: str, s: dict) -> str:
    if s["n"] == 0:
        return f"{name:<22} —"
    return (
        f"{name:<22} {s['n']:>5} {s['pnl_usd']:>+9.2f} {s['pnl_bps_mean']:>+7.2f} {s['winrate']:>6.0%} "
        f"{s['max_dd_usd']:>8.2f} {s['sharpe_trade']:>6.2f} {s['pos_plan_usd']:>7.0f} {s['pos_fact_usd']:>8.2f}"
    )


HEAD = f"{'срез':<22} {'сделок':>5} {'P&L $':>9} {'bps/сд':>7} {'win':>6} {'просадка':>8} {'t-стат':>6} {'поза$':>7} {'факт$':>8}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid-dir", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--order-usd", type=float, default=1000.0)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", default=None)
    ap.add_argument("--by-fill", action="store_true", help="P&L на исполненную долю (fill_frac), не на полный лот")
    ap.add_argument("--grid-dir2", default=None, help="второй каталог (другая сторона) — строки складываются, сторона по dir")
    ap.add_argument("--verdict", default=None, help="CSV вердикта bounce-verdict: итог печатается первой строкой")
    ap.add_argument("--mids", default=None, help="кэш касаний с mids1m (study/touches): контроль «рост рынка»")
    a = ap.parse_args()
    global BY_FILL
    BY_FILL = a.by_fill
    rows = load_rounds(f"{a.grid_dir}/rounds.csv", a.form)
    if a.grid_dir2:
        rows += load_rounds(f"{a.grid_dir2}/rounds.csv", a.form)
        rows.sort(key=lambda x: x["t0_ns"])
    if not rows:
        print(f"нет сделок формы {a.form} в {a.grid_dir}")
        return 1
    out: dict = {"grid": a.grid_dir, "form": a.form, "order_usd": a.order_usd}

    def section(title: str, key, sort_by_pnl: bool = False, top: int | None = None):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        items = [(k, stats(v, a.order_usd)) for k, v in groups.items()]
        items.sort(key=(lambda kv: -kv[1]["pnl_usd"]) if sort_by_pnl else (lambda kv: kv[0]))
        if top:
            items = items[:top]
        print(f"\n== {title}")
        print(HEAD)
        for k, s in items:
            print(fmt_row(str(k), s))
        out[title] = {k: s for k, s in items}

    total = stats(rows, a.order_usd)
    if a.verdict:
        itog = "?"
        with open(a.verdict, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "ИТОГ:" in line:
                    itog = line.split("ИТОГ:", 1)[1].strip()
        print(f"ИТОГ ВЕРДИКТА: {itog} — деньги ниже не доказательство, пока итог не «зелёный»")
    print(f"Форма {a.form}; {a.grid_dir}{' + ' + a.grid_dir2 if a.grid_dir2 else ''}; лот ${a.order_usd:.0f} "
          f"{'× исполненная доля' if BY_FILL else 'полный (В-83)'}")
    # Капитал под пик одновременных позиций и концентрация по монетам (аудит 22.09 §0 п. 5–6).
    ev = []
    for r in rows:
        ev.append((r["t0_ns"], 1))
        ev.append((r["exit_ns"] or r["t0_ns"], -1))
    ev.sort()
    cur = peak = 0
    for _, d in ev:
        cur += d
        peak = max(peak, cur)
    by_coin: dict[str, float] = defaultdict(float)
    for r in rows:
        by_coin[r["symbol"]] += trade_pnl(r, a.order_usd)
    top4 = sorted(by_coin.items(), key=lambda kv: -kv[1])[:4]
    top4_sum = sum(v for _, v in top4)
    print(f"Пик одновременных позиций {peak} → капитал ≈ ${peak * a.order_usd:.0f}, P&L на капитал "
          f"{(total['pnl_usd'] / (peak * a.order_usd) * 100 if peak else 0):+.2f} %; монет {len(by_coin)}, топ-4 "
          f"({', '.join(k for k, _ in top4)}) дают {top4_sum:+.2f} $ из {total['pnl_usd']:+.2f}, остальные {total['pnl_usd'] - top4_sum:+.2f} $")
    if a.mids:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("placebo", os.path.join(os.path.dirname(os.path.abspath(__file__)), "placebo.py"))
        pl = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(pl)
        ctl = pl.control([{**r, "form": a.form} for r in rows], pl.Mids(a.mids))
        if ctl:
            sm = pl.summary(ctl)
            print(f"Контроль «рост рынка» (та же монета, тот же день, то же удержание, случайная минута): "
                  f"{sm['control_bps']:+.2f} bps; превышение {sm['excess_bps']:+.2f} (медиана {sm['excess_median_bps']:+.2f}) bps "
                  f"= {sm['excess_bps'] / 1e4 * a.order_usd * sm['n']:+.2f} $; > 0 в {sm['days_excess_pos']} сутках из {sm['days']}")
    print(f"Сделок {total['n']}, дней {total['days']}, P&L {total['pnl_usd']:+.2f} $ ({total['pnl_bps_mean']:+.2f} bps/сделка), "
          f"winrate {total['winrate']:.0%}, средний плюс {total['avg_win']:+.2f} / минус {total['avg_loss']:+.2f} $, "
          f"макс. просадка {total['max_dd_usd']:.2f} $, t-статистика по сделкам (как независимым, не Шарп) {total['sharpe_trade']:.2f}, "
          f"Шарп по дням (годовой; при < 30 днях — шум) {total['sharpe_day']:.2f}, "
          f"поза план {total['pos_plan_usd']:.0f} $ (исполнено {total['fill_frac']:.0%}), факт при лоте пула {total['pos_fact_usd']:.2f} $, "
          f"удержание медиана {total['hold_min_med']:.0f} мин")
    out["total"] = total
    section("по дням", lambda r: r["day"])
    section("по неделям (ISO)", lambda r: dt.date.fromisoformat(r["day"]).strftime("%G-W%V"))
    section(f"по монетам (топ {a.top} по P&L)", lambda r: r["symbol"], sort_by_pnl=True, top=a.top)
    section("по стороне", lambda r: "long (bid)" if r["dir"] > 0 else "short (ask)")
    section("по дням × сторона", lambda r: f"{r['day']} {'long' if r['dir'] > 0 else 'short'}")
    section("по типу выхода", lambda r: r["reason"])
    section("по ногам лестницы", lambda r: f"ног исполнено {r['legs_filled']}")
    section("по исполнению", lambda r: "полное (100 %)" if r["fill_frac"] >= 0.999 else "частичное")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
