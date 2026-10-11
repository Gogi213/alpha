#!/usr/bin/env python3
"""T-32 approach-slices §7-busy / §7.x (CEO 27.09, два дополнения к срезам по номеру подхода):

§7-busy — правила «только N-й / первые K / с N-го» ПЕРЕСЧИТАНЫ с занятостью монеты через busy-replay.py
(T-31, гейт зелёный, на проверке у Судьи): сигналы (не только исполненные сделки) связаны со стеной тем же
способом, что сделки (`tmp-t32/slices_features.py`/`tmp-t32/busy_rules.py` на Steam Deck), отфильтрованы
правилом, пропущены через `busy-replay.py` по всем трём домам, посчитаны `portfolio-sim.py`. Сверка: «все,
без фильтра» дало 503/155 сделок и +$106/+$111 -- байт в байт с базой (`busy_rules.py` печатает `SANITY`).
Результат — `data/t32/busy/busy-rules.json` (+ `ps.json`/`ps-closes.json` на правило в `data/t32/busy/busy/r<i>`,
`r0`=«все (номер известен)», `r9`=«первые 3», `r10`=«первые 4», `r15`=«с 4-го и дальше», `r1..r6`=«только N-й»).

§7.x — вопрос владельца («после 4 подхода масштабируется просадка? при торговле с плечом это фатально»):
  1) максимальная просадка $, доля часов под водой глубже $50 (В-123, справочно), худшие сутки $ -- для
     «все»/«первые 3»/«первые 4»/«с 4-го» -- с занятостью (busy-replay), `kn.drawdown_stats`/`hourly_underwater`
     (снимок kpi-newhigh.py c8f274d) по закрытиям;
  2) вклад сделок подхода 4+ в топ-3 самых глубоких эпизода просадки БАЗЫ (все 658 сделок, `main-trades.csv` +
     номер из `slices-trades.csv`, БЕЗ пересчёта занятости -- локальные данные): сколько сделок 4+ и их $ в
     каждом эпизоде; сутки «стоп 4+ вместе с другими стопами» против «стоп 4+ один»;
  3) стресс плеча x2/x3 для «все»/«первые 3» (busy-replay): худшие сутки и макс. просадка в % депозита $2500,
     позиция $1000/$1500 линейным масштабом (не точный прогон); stress_pct portfolio-sim (обвал на пике позиций,
     -59.7%) рядом, x2/x3 -- тем же масштабом. Сверка со значением владельца: «все» на $500 -- -173% август,
     -96% сентябрь (см. вывод SANITY).

    python tools/compute/t32-busy-stress.py --append data/t32/approach-slices-summary.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os

BUSY_DIR = "data/t32/busy"
MAIN = "data/t32/main-trades.csv"
SLICES = "data/t32/slices-trades.csv"
DD50_USD = 50.0
RULE_FOLDER = {"все (номер известен, busy-replay)": "r0", "первые 3 (busy-replay)": "r9",
               "первые 4 (busy-replay)": "r10", "с 4-го и дальше (busy-replay)": "r15"}


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def grid_row(rule_dir, epoch):
    d = json.load(open(os.path.join(BUSY_DIR, "busy", rule_dir, "ps.json"), encoding="utf-8"))
    for g in d["grid"]:
        if g["epoch"] == epoch:
            return g
    return None


def closes_of(rule_dir, epoch):
    d = json.load(open(os.path.join(BUSY_DIR, "busy", rule_dir, "ps-closes.json"), encoding="utf-8"))
    return [tuple(x) for x in d["т32"].get(epoch, {}).get("0", [])]


def hourly_share_gt(kn, closes, thresh):
    h = kn.hourly_underwater(closes)
    out = {}
    for pk in ("aug", "sep"):
        xs = h[pk]
        out[pk] = round(sum(1 for x in xs if x > thresh) / len(xs), 3) if xs else None
    return out


def load_local_trades():
    main = {}
    with open(MAIN, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            main[(r["month"], r["sym"], int(r["t0_ms"]))] = r
    N = {}
    with open(SLICES, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["month"], r["sym"], int(r["t0_ms"]))
            if r["status"] == "linked" and r.get("approach_number_at_entry") not in (None, ""):
                N[key] = int(float(r["approach_number_at_entry"]))
    rows = []
    for key, r in main.items():
        rows.append({"key": key, "t0": int(r["t0_ms"]), "t1": int(r["t1_ms"]), "pnl": float(r["pnl_usd"]),
                      "reason": r["reason"], "day": dt.datetime.fromtimestamp(int(r["t0_ms"]) / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                      "N": N.get(key)})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--append", default="data/t32/approach-slices-summary.md")
    ap.add_argument("--out", default="data/t32/busy-stress.json")
    a = ap.parse_args()

    kn = load_mod("tools/compute/kpi-newhigh.py", "kn")
    br = json.load(open(os.path.join(BUSY_DIR, "busy-rules.json"), encoding="utf-8"))

    L = ["", "## 7-busy. Правила «только N-й / первые K / с N-го» С ПЕРЕСЧЁТОМ ЗАНЯТОСТИ (busy-replay, T-31, "
             "гейт зелёный — на проверке у Судьи)", "",
         f"Связь сигнал -> номер подхода (не только исполненные сделки): всего сигналов {br['n_signals_total']}, "
         f"с известным номером {br['n_signals_numbered']} (без номера — {br['n_signals_total']-br['n_signals_numbered']}, "
         "исключены из правил совсем, как и у сделок). **Сверка:** правило «все, без фильтра» дало "
         f"{'СОВПАЛО' if br['sanity_ok'] else 'НЕ СОВПАЛО'} с базой (503/155 сделок, +$106/+$111) — "
         f"aug n={br['sanity_all']['aug']['n']} ${br['sanity_all']['aug']['usd']:+.0f}, "
         f"sep n={br['sanity_all']['sep']['n']} ${br['sanity_all']['sep']['usd']:+.0f}.", "",
         "| правило | авг n | авг $ | авг >5сут | авг на_пике/просадка | сен n | сен $ | сен >5сут | сен на_пике/просадка |",
         "|---|---|---|---|---|---|---|---|---|"]
    keep_order = ["все (номер известен, без исключения по правилу)"] + [f"только {i}-й" for i in range(1, 7)] + \
                 [f"первые {i}" for i in range(1, 7)] + [f"с {i}-го и дальше" for i in range(2, 7)]
    for name in keep_order:
        r = br[name]
        def cell(m):
            x = r[m]
            gt = f"{x['frac_gt_5d']*100:.0f}%" if x['frac_gt_5d'] is not None else "—"
            return f"{x['n']} | {x['usd']:+.0f} | {gt} | {x['at_peak_share']*100:.0f}%/{x['below_peak_share']*100:.0f}%"
        L.append(f"| {name} | " + cell("aug") + " | " + cell("sep") + " |")
    L.append("\nБез занятости (§7 выше, отбор сделок из готового прогона) против с занятостью — числа расходятся: "
              "например «только 1-й» без занятости авг n=134, с занятостью (busy-replay) n=277 — при исключении "
              "сделок ПОЗЖЕ монета освобождается раньше и в неё входит следующий сигнал этого же правила, которого "
              "без пересчёта занятости в проходной выборке не было. Занятость меняет не только KPI, но и сам состав "
              "сделок правила.")

    # §7.x
    L += ["", "## 7.x. Просадка и плечо после 4-го подхода (вопрос владельца)", ""]
    L.append("### 1) Просадка/просадочные часы с занятостью (busy-replay) — «все» / «первые 3» / «первые 4» / «с 4-го»\n")
    L.append("| правило | месяц | макс. просадка $ | часов под водой >$50 | худшие сутки $ |")
    L.append("|---|---|---|---|---|")
    stress_rows = {}
    for name, rd in RULE_FOLDER.items():
        stress_rows[name] = {}
        for m, epoch in (("aug", "август"), ("sep", "сентябрь")):
            g = grid_row(rd, epoch)
            cl = closes_of(rd, epoch)
            share = hourly_share_gt(kn, cl, DD50_USD)[m] if cl else None
            stress_rows[name][m] = g
            sh = f"{share*100:.0f}%" if share is not None else "—"
            L.append(f"| {name} | {m} | {g['dd_usd']:.0f} | {sh} | {g['worst_day_usd']:+.0f} |")
    L.append("")

    # 2) contribution of N>=4 trades to top-3 deepest base episodes (local, no occupancy recompute)
    trades = load_local_trades()
    base_closes = [(t["t1"], t["pnl"]) for t in trades]
    dd = kn.drawdown_stats(base_closes)
    all_eps = []
    for m in ("aug", "sep"):
        for e in dd[m]["episodes"]:
            e2 = dict(e)
            e2["month"] = m
            all_eps.append(e2)
    all_eps.sort(key=lambda e: -e["depth_usd"])
    top3 = all_eps[:3]
    L += ["### 2) Вклад сделок подхода 4+ в топ-3 самых глубоких эпизода просадки БАЗЫ (без пересчёта занятости, "
          "локальные 658 сделок)\n", "| эпизод (пик -> дно) | глубина $ | сделок 4+ в окне | их $ |", "|---|---|---|---|"]

    def in_window(t, e):
        t_peak_iso, t_low_iso = e["t_peak"], e["t_low"]
        return t_peak_iso <= dt.datetime.fromtimestamp(t["t1"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") <= t_low_iso

    for e in top3:
        in_e = [t for t in trades if in_window(t, e)]
        n4 = [t for t in in_e if t["N"] is not None and t["N"] >= 4]
        L.append(f"| {e['t_peak']} -> {e['t_low']} | {e['depth_usd']:.0f} | {len(n4)} из {len(in_e)} | "
                  f"{sum(t['pnl'] for t in n4):+.1f} |")
    stop_days_n4 = set(t["day"] for t in trades if t["reason"] == "stop" and t["N"] is not None and t["N"] >= 4)
    stop_days_other = set(t["day"] for t in trades if t["reason"] == "stop" and (t["N"] is None or t["N"] < 4))
    together = stop_days_n4 & stop_days_other
    alone = stop_days_n4 - stop_days_other
    L.append(f"\nСутки со стопом подхода 4+: {len(stop_days_n4)}; из них вместе со стопом другого подхода в те же "
              f"сутки — {len(together)}, «один» (только стопы 4+) — {len(alone)}.")

    # 3) leverage stress x2/x3
    L += ["", "### 3) Стресс плеча x2/x3 (линейный масштаб позиции, НЕ точный прогон) — «все» / «первые 3», "
              "busy-replay\n", "| правило | месяц | x | худшие сутки % депозита | просадка % депозита | "
              "stress_pct (обвал на пике) |", "|---|---|---|---|---|---|"]
    for name in ("все (номер известен, busy-replay)", "первые 3 (busy-replay)"):
        for m, epoch in (("aug", "август"), ("sep", "сентябрь")):
            g = grid_row(RULE_FOLDER[name], epoch)
            for x in (1, 2, 3):
                L.append(f"| {name} | {m} | x{x} | {g['worst_day_pct']*x:+.1f}% | -{g['dd_pct']*x:.2f}% | "
                          f"-{g['stress_pct']*x:.1f}% |")
    sanity_aug_stress = grid_row("sanity-all", "август")["stress_pct"]
    sanity_sep_stress = grid_row("sanity-all", "сентябрь")["stress_pct"]
    L.append("\nСверка со значением владельца (стресс на $500, без плеча): «все, без фильтра» (полная база 503/155) "
              f"дало август stress_pct {sanity_aug_stress:.1f}%, сентябрь {sanity_sep_stress:.1f}% — против "
              f"ожидаемых владельцем -173%/-96% — {'СОВПАЛО' if abs(sanity_aug_stress-173.0) < 1 else 'РАСХОДИТСЯ'}.")
    L.append("\nОговорки: плечо x2/x3 здесь — линейный масштаб денег (не пересчитан engine-очередью/ликвидацией); "
              "просадка/стоп-анализ п.2 — без пересчёта занятости (те же 658 сделок готового прогона, номер подхода "
              "локальный); п.1 и п.3 — с пересчётом занятости через busy-replay, но по подмножеству сигналов с "
              "известным номером (без ambiguous/unknown) — не байт-в-байт равно локальной базе 658 сделок.")

    with open(a.append, "a", encoding="utf-8", newline="") as f:
        f.write("\n".join(L) + "\n")
    json.dump({"rules_kpi": {k: br[k] for k in keep_order}, "stress": stress_rows, "top3_episodes": top3,
               "stop_days_n4_together": len(together), "stop_days_n4_alone": len(alone)},
              open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
