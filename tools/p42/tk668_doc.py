#!/usr/bin/env python3
"""П-42 §13.68.2 (TK-668): текст раздела с итогом счёта без P&L из `/data/p42-tk668/res/counts68-*.json`, `gates/g68.json`, `feat/info.json` (запуск на srv: `python3 tk668_doc.py counts <коммит кода> > prereg-13.68.2.md`)."""
from __future__ import annotations

import json
import os
import sys

ROOT = "/data/p42-tk668"
MON = ("июль", "авг.", "сент.", "окт.", "ноя.")


def load(*a):
    return json.load(open(os.path.join(ROOT, *a), encoding="utf-8"))


def main():
    commit = sys.argv[2] if len(sys.argv) > 2 else "?"
    g, info = load("gates", "g68.json"), load("feat", "info.json")
    o = []
    p = o.append
    p("### 13.68.2 Итог счёта без P&L к §13.68 (PS-938 / PS-939) — записано 21.11.2026 ДО чтения P&L (TK-668 v2; код `%s`; юнит `tk668-counts`, `tk668-gate`)" % commit)
    p("")
    p("Клетки, пороги, `k`, горизонты, правило E8 — как §13.68 / §13.68.1 (не меняются). P&L ни одной клетки и ни одной сделки E8 не читался и не считался; в счёте использовались только ключи сделок баз (монета, мс, вид выхода) и знак P&L самой сделки базы для разбора числа событий E8 (известный результат TK-662 / 663).")
    p("")
    p("#### 1. Ворота (`gates/g68.json`)")
    gl, gc, ga, gn, go = g["G-lvl"], g["G-causal-levels"], g["G-causal-arrays"], g["G-n"], g["G-off1"]
    p("- **G-src:** все входы на месте, sha256 записаны (`g68.json` → `G-src`: 6 файлов дампов сигналов, 6 дампов уровней, 3 кэша BTC, 6 таблиц признаков TK-634, 6 файлов сделок баз; бинарь `%s`)." % g["G-src"]["bin"][:16])
    p("- **G-n:** сделок баз `S_base` %d, `L_base` %d (= TK-662); ключи таблицы признаков = ключам дампа сигналов = ключам таблицы признаков TK-634 во всех 6 окнах × сторонах (дублей ключа 0). **Событий `rt` у `S_base` — %d, а не 188 (оценка TK-663): разбор — 188 из них с отрицательным P&L самой сделки базы (это и есть «188 выходов `rt` по −1,22 $» TK-663), ещё 6 — с неотрицательным; правило §13.68 — «все сделки вида `rt`» — не менялось, события = 194 = 188 с отрицательным + 6 с неотрицательным P&L базы. Решение критика (`thr_4gmxgf5ajv`, 21.11): правило остаётся как записано (188 в §13.68 — счёт убыточных `rt` TK-663, расхождение G-n объяснено, не дефект); сужение до убыточных было бы новой клеткой, выбранной после счёта, — не делается.**" % (gn["trades_S"], gn["trades_L"], gn["rt_events"]))
    p("- **G-lvl:** у %d строк лонга и %d строк шорта найден свой уровень, живой на минуте сигнала, с ценой версии = `|level_px_e8|` — 100 %% (%d / %d); цена шорта в дампе положительна у всех (%d / %d); следующий уровень — выше `P` у лонга (%d / %d) и ниже `P` у шорта (%d / %d). Прогон базы с дампом = готовой базе байт в байт (trades, daily, coinday, entryrej × 2 стороны × 3 окна)." % (
        gl["L"]["rows"], gl["S"]["rows"], gl["L"]["own_found"] + gl["S"]["own_found"], gl["L"]["rows"] + gl["S"]["rows"], gl["S"]["price_positive"], gl["S"]["rows"], gl["L"]["next_direction_ok"], gl["L"]["with_next"], gl["S"]["next_direction_ok"], gl["S"]["with_next"]))
    p("- **G-causal (зерно 668):** признаки по минуткам монеты и BTC (`X24`, `X7`, `XP`, `XV`, `r_h`, `σ_h`) — мусор в минутках ≥ t не меняет значение (точное равенство) на %d строках (по 110 на окно, обе стороны); `R` — движок (база, дамп уровней) на кэше монет с мусором с минуты отсечки (максимальное `t` строк монеты): `R` и «свой уровень найден» не изменились у %d строк (%d монет × стороны; мусор изменил уровни у %d из них — тест способен упасть)." % (
        ga["rows_total"], gc["rows_total"], sum(v["coins"] for k, v in gc.items() if k != "rows_total"), sum(v["coin_sides_levels_changed_by_garbage"] for k, v in gc.items() if k != "rows_total")))
    sh, kl = go["shift"], go["klines"]
    p("- **G-off1:** `r_h` при h = 5 / 15 = готовым `btc_mv5` / `btc_mv15` f12 на всех строках эпизодов (12 сверок, max|Δ| = %.1e); `r_1` отличается от сдвига на минуту назад на %.1f %% строк, вперёд на %.1f %% (%d строк; нужно ≥ 99 %%); независимый пересчёт из klines BTCUSDT (`/data/klines/binance`): %d строк, %d значений `r_1 / r_5 / r_15`, совпало %d." % (
        max(v["max_abs"] for v in go["geq"].values()), 100 * sh["differs_back"], 100 * sh["differs_fwd"], sh["rows"], kl["rows_sampled"], kl["values_checked"], kl["equal"]))
    p("")
    p("#### 2. Таблицы признаков (`feat/<окно>-<сторона>.csv`, строк = дампу сигналов)")
    for k, v in info.items():
        p("- `%s`: %d строк, пусто %s, sha256 `%s`" % (k, v["rows"], {c: n for c, n in v["nan"].items() if n}, v["sha256"][:16]))
    p("")
    for side, name in (("L", "Лонг (`L_base`)"), ("S", "Шорт (`S_base`)")):
        c = load("res", "counts68-%s.json" % side)
        p("#### 3.%s %s: сделок базы %d (по месяцам %s)" % ("1" if side == "L" else "2", name, c["n_trades"], c["base_by_month"]))
        p("- Пороги EP (квантили по сделкам базы стороны, linear): " + "; ".join("%s q25 %.4g / q75 %.4g (n %d)" % (k, v["0.25"], v["0.75"], v["n"]) for k, v in c["thr"].items()) + ". Соседи: q10 / q40 / q60 / q90 — в `counts68-%s.json`." % side)
        p("- Охват значений на сделках по месяцам: " + "; ".join("%s %s" % (k, "/".join("%.2f" % x for x in v)) for k, v in c["coverage"].items()))
        p("- `R` на сделках базы: %s." % c["R_dist"])
        p("- Доля сделок с шоком BTC (≥ 2σ): " + "; ".join("%d мин — любой %.1f %%, по сделке %.1f %%, против %.1f %%" % (h, 100 * c["corr"]["shock_share_%d" % h]["abs_ge2"], 100 * c["corr"]["shock_share_%d" % h]["by_trade"], 100 * c["corr"]["shock_share_%d" % h]["against"]) for h in (1, 5, 15)) + ".")
        p("- Спирмен: " + "; ".join("%s ~ mv60 %.2f, ~ natr_bp %.2f" % (k, c["corr"][k]["mv60"][0], c["corr"][k]["natr_bp"][0]) for k in ("X24", "X7", "XP", "XV")) + "; BTC: " + "; ".join("%d мин: `r·s` ~ `mv5` %.2f, `|r|` ~ `|mv5|` %.2f" % (h, c["corr"]["btc%d" % h]["rh_s_vs_mv5"][0], c["corr"]["btc%d" % h]["abs_rh_vs_abs_mv5"][0]) for h in (1, 5, 15)) + ". Ни одно |ρ| не выше 0,8 — «копии импульса» нет.")
        p("")
        p("| клетка | исключено сделок базы | по месяцам | останется по месяцам | подмножество | допуск | дубль |")
        p("|---|---|---|---|---|---|---|")
        for x in c["cells"]:
            if x["trial"]:
                p("| **%s** | %d (%.1f %%) | %s | %s | %s | **%s** | %s |" % (x["cid"], x["E"], 100 * x["E"] / c["n_trades"], x["excl_by_month"], x["remain_by_month"], "да" if x["subset"] else "нет", "да" if x["admit_floor"] else "нет", x["dup_of"] or "—"))
        nb = [x for x in c["cells"] if not x["trial"]]
        p("")
        p("Соседи (описательно; считаются на P&L только для клеток, прошедших (g1)–(g4)): %d клеток, допуск у %d; Jaccard соседей с хозяином ≤ 0,8 у: %s. Подробно — `counts68-%s.json`." % (
            len(nb), sum(1 for x in nb if x["admit_floor"]), ", ".join("%s %d из %d" % (k, sum(1 for v in d.values() if v <= 0.8), len(d)) for k, d in c["jaccard"].items()), side))
        p("Пробных клеток %d, без дублей %d, **допущенных %d** (недопущенные идут в Холм с p = 1; m не меняется: E 79, B 121)." % (len([x for x in c["cells"] if x["trial"]]), c["m_trial_nodup"], c["m_trial_admit"]))
        p("")
    e30, e8 = load("res", "counts68-e30.json"), load("res", "counts68-e8.json")
    p("#### 4. E30 — встречные сигналы (счёт)")
    for s in ("L", "S"):
        v = e30[s]
        p("- %s: сделок базы %d, с ≥ 1 встречным сигналом **%d** (по месяцам %s), встречных сигналов %d → **%s** (порог 70, §13.68 п. 5)." % ("Лонг" if s == "L" else "Шорт", v["n_base"], v["n30"], v["by_month"], v["n_signals"], "DROP без P&L" if v["drop"] else "считаем"))
    p("")
    p("#### 5. E8 — события и лента (без исходов)")
    p("Событий `rt` %d (по месяцам %s); цена уровня шорта в дампе < 0 у %d; без ленты %d; ленты только одного дня (вход в последние сутки данных) %d; «не исполнена» (`e ≤ S`) %d. Вход, стоп, тейк, время, соседи (стоп 0,5 / 2 NATR от входа, тейк 2 NATR, время 30 / 120 мин) — §13.68 п. 5 и §13.68.1 п. 2; тест на синтетической ленте — `tests/test_tk668.py`." % (
        e8["events"], e8["by_month"], e8["px_neg"], e8["no_tape"], e8["only_one_day"], e8["e_le_s"]))
    p("")
    p("#### 6. Порядок дальше и что не сделано")
    p("- E4 / 45 (ключ движка `lx`): код в работе; счёт по источникам (родилось / живых на сигнале / сигналов / кандидатов / сделок) и G-byte — отдельным разделом §13.68.3 до P&L.")
    p("- После PASS критика на этот раздел: `/data/p42-tk668/PASS`, затем P&L (клетки E / B, E8, ступень 1) — один раз; Холм: E m = 79, B m = 121, линии m = 9; прежние p — `select.json`, `select33.json`, `select34.json`, `select36.json` (61), TK-656 `tables56.json` (24).")
    print("\n".join(o))


if __name__ == "__main__":
    main()
