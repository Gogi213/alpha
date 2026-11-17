"""П-42 §13.17 (TK-588): таблицы отчёта в Markdown из results.json / auc.csv / quintiles.csv / grid.csv (только форматирование, статистика не считается).
Использование: python3 sigfeat_report.py <рабочий каталог> > tables.md"""
from __future__ import annotations

import json
import math
import sys

import pandas as pd

W = sys.argv[1]
res = json.load(open(f"{W}/results.json", encoding="utf-8"))
auc = pd.read_csv(f"{W}/auc.csv")
q = pd.read_csv(f"{W}/quintiles.csv")
grid = pd.read_csv(f"{W}/grid.csv")
SIDES = (("long", "лонг"), ("short", "шорт"))
PRIMARY = {"чистый уровень / зона": ["pierce_ep", "spread_tau"], "разреженность": ["emp6h", "tpm60"], "резкость подхода и проторговка": ["mv5", "cons60"],
           "корреляция с BTC / ETH": ["c5_btc_7d", "c5_eth_7d"]}
NAME = {"pierce_ep": "число проколов уровня (`pierce_ep`)", "spread_tau": "разброс пивотов в τ (`spread_tau`)", "emp6h": "доля пустых минут за 6 ч (`emp6h`)",
        "tpm60": "сделок в минуту за 60 мин (`tpm60`)", "mv5": "ход к уровню за 5 мин, в NATR (`mv5`)", "cons60": "минут проторговки у уровня за 60 мин (`cons60`)",
        "c5_btc_7d": "корреляция с BTC, 5-мин, 7 сут. (`c5_btc_7d`)", "c5_eth_7d": "корреляция с ETH, 5-мин, 7 сут. (`c5_eth_7d`)"}


def pc(x, d=1):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{d}f} %"


def pp(x, d=1):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:+.{d}f} п.п."


def f3(x):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def lst(x):
    try:
        a, b = json.loads(x) if isinstance(x, str) else x
        return f"[{a:.3f}; {b:.3f}]"
    except Exception:
        return "—"


def ci(lo, hi, f=pc):
    return f"[{f(lo)}; {f(hi)}]"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    P = print
    # ── размеры
    P("### Размеры\n")
    rows = []
    for s, nm in SIDES:
        r = res[s]
        g = r["signals"]
        rows.append([nm, g["signals_u1"], g["episodes_u1"], g["levels_with_signal"], g["repeat_episodes"], r["n_label"], r["episodes_u0"], r["icc"]["n_days"], r["icc"]["n_coins"],
                     r["icc"]["episodes_per_coin_max"]])
    P(table(["сторона", "сигналов U1", "эпизодов U1", "уровней со ≥ 1 сигналом", "повторных эпизодов", "эпизодов с меткой (ok_t60)", "эпизодов U0", "суток", "монет", "макс. эпизодов на монету"], rows))
    P("\nМинимальный различимый эффект AUC (до чтения меток записан в §13.17): " + "; ".join(f"{nm}: " + ", ".join(f"p={p}: {v:.3f}" for p, v in res[s]["mde_auc"].items()) for s, nm in SIDES))
    # ── метка
    P("\n### Метка O3 (X = 1 %, T = 60 мин от сигнала, опорная — среднее пивотов), U1\n")
    rows = []
    for s, nm in SIDES:
        r = res[s]
        sh = r["outcome_shares_x1_t60"]
        rows.append([nm, r["n_label"], pc(r["rate_o3"]), ci(*r["rate_o3_wilson"]), ci(*r["rate_o3_ci_day"]), ci(*r["rate_o3_ci_coin"]), pc(sh["O1"]), pc(sh["O2"]), pc(sh["O4"]),
                     pc(r["control"]["o3_x1_mean"]), pp(r["rate_o3"] - r["control"]["o3_x1_mean"]), pc(r["control"]["touch_x1_mean"]), pc(r["touch_rate"])])
    P(table(["сторона", "n", "O3 «пробой»", "Уилсон 95 %", "блоки по суткам 95 %", "по монетам 95 %", "O1 «не дошла»", "O2 «касание»", "O4 «возврат»", "контроль O3", "O3 − контроль", "контроль: дошла до уровня", "дошла до уровня"], rows))
    P("\nЭффективное число (по метке): " + "; ".join(f"{nm}: сутки ρ={res[s]['icc']['day']:.3f}, N_eff={res[s]['icc']['neff_day']:.0f}; монеты ρ={res[s]['icc']['coin']:.3f}, N_eff={res[s]['icc']['neff_coin']:.0f}" for s, nm in SIDES))
    P("\n#### Сетка метки (доли исходов, U1)\n")
    g2 = grid.copy()
    rows = [[("лонг" if r.side == "long" else "шорт"), ("среднее пивотов" if r.ref == "mean" else "крайний пивот"), r.label, int(r.T), int(r.n), pc(r.O1), pc(r.O2), pc(r.O3), pc(r.O4)] for r in g2.itertuples()]
    P(table(["сторона", "опорная", "X", "T, мин", "n", "O1", "O2", "O3", "O4"], rows))
    # ── чистый / зона
    P("\n### Чистый уровень и зона (U1)\n")
    rows = []
    for s, nm in SIDES:
        z = res[s]["zone_clean"]
        for key, lab in (("clean_pierce0", "чистый (`pierce_ep` = 0)"), ("zone_pierce_ge1", "зона (`pierce_ep` ≥ 1)"), ("in_pk", "правило `pk` выполнено"), ("not_in_pk", "`pk` не выполнено"),
                         ("in_ow", "в наборе `ow`"), ("not_in_ow", "вне `ow`")):
            v = z[key]
            rows.append([nm, lab, v["n"], v["k"], pc(v["rate"]), ci(*v["wilson"]), pc(v["outcomes"]["O1"]), pc(v["outcomes"]["O2"]), pc(v["outcomes"]["O4"]), pc(v["ctrl"])])
    P(table(["сторона", "группа", "эпизодов", "из них O3", "доля O3", "Уилсон 95 %", "O1", "O2", "O4", "контроль O3"], rows))
    P("\nРазность (зона − чистый) и (`pk` − не `pk`):\n")
    rows = []
    for s, nm in SIDES:
        for key, lab in (("zone_minus_clean", "зона − чистый"), ("in_pk_minus_not", "`pk` − не `pk`")):
            for hh in ("ALL", "A", "B"):
                v = res[s].get(f"{key}_{hh}", {})
                rows.append([nm, lab, {"ALL": "все 60 сут.", "A": "A", "B": "B"}[hh], v.get("n_g1"), v.get("n_g0"), pc(v.get("rate_g1")), pc(v.get("rate_g0")), pp(v.get("diff")),
                             ci(*v["ci_day"], f=pp) if v.get("ci_day") else "—", ci(*v["ci_coin"], f=pp) if v.get("ci_coin") else "—", f3(v.get("p"))])
    P(table(["сторона", "сравнение", "часть", "n группы 1", "n группы 0", "доля O3 группы 1", "доля O3 группы 0", "разность", "блоки по суткам 95 %", "по монетам 95 %", "p"], rows))
    # ── разреженность
    P("\n### Разреженность\n")
    rows = []
    for s, nm in SIDES:
        for univ in ("U1", "U0"):
            for nmk, lab in (("emp6h_top20", "верхний квинтиль `emp6h`"), ("tpm60_bottom20", "нижний квинтиль `tpm60`")):
                v = res[s]["sparse"][f"{univ}_{nmk}"]
                rows.append([nm, univ, lab, f"{v['threshold']:.4g}", v["n_Z"], v["n_rest"], pc(v["rate_Z"]), pc(v["rate_rest"]), pp(v["diff_rest_minus_Z"]),
                             ci(*v["ci_diff_day"], f=pp) if v["ci_diff_day"] else "—", ci(*v["ci_diff_coin"], f=pp) if v["ci_diff_coin"] else "—", pc(v["explained_share_of_nonbreaks"]),
                             pp(v["excess_Z"]) if v["excess_Z"] is not None else "—", pp(v["excess_rest"]) if v["excess_rest"] is not None else "—"])
    P(table(["сторона", "вселенная", "группа Z", "граница", "n в Z", "n вне Z", "O3 в Z", "O3 вне Z", "вне − в Z", "блоки по суткам 95 %", "по монетам 95 %", "доля непробоев, объяснённая (модель)", "O3 − контроль в Z", "O3 − контроль вне Z"], rows))
    P("\nМонета против момента (AUC для O3):\n")
    rows = []
    for s, nm in SIDES:
        for univ in ("U1", "U0"):
            sp = res[s]["sparse"]
            rows.append([nm, univ, sp[f"{univ}_n"], f3(sp[f"{univ}_auc_emp6h"]), f3(sp[f"{univ}_auc_coin_level"]), f3(sp[f"{univ}_auc_moment_part"])])
    P(table(["сторона", "вселенная", "эпизодов", "AUC `emp6h`", "AUC доли пустых минут монеты за 60 сут.", "AUC момента (`emp6h` − монета)"], rows))
    P("\nСделки `ow_e` в разреженной группе Z (верхний квинтиль `emp6h`, граница U1) и вне (медиана не пишется):\n")
    rows = []
    for s, nm in SIDES:
        t = res[s]["trades_ow_e"]
        for k_, lab in (("all", "все"), ("Z", "в Z"), ("rest", "вне Z")):
            v = t[k_]
            rows.append([nm, lab, v["n"], f"{v['sum']:+.0f}", f"{v['mean']:+.2f}", pc(v["stop_share"]), pc(v["neg_share"]), f"{v['neg_sum']:.0f}"])
        rows.append([nm, "без значения `emp6h`", t["no_value"], "", "", "", "", ""])
    P(table(["сторона", "группа", "сделок", "сумма $", "среднее $ на сделку", "доля стопов", "доля сделок с отрицательным итогом", "сумма отрицательных $"], rows))
    # ── первичный пакет и квинтили
    P("\n### Первичный пакет: AUC признака против метки O3 (Холм по 8 признакам стороны)\n")
    rows = []
    for s, nm in SIDES:
        t = auc[(auc.side == s) & (auc.group == "primary")]
        for r in t.itertuples():
            rows.append([nm, NAME[r.feature], r.n_valid, f3(r.auc), lst(r.ci_day), lst(r.ci_coin), f3(r.p), f3(r.p_holm), f"{f3(r.auc_A)} {lst(r.ci_A)}", f"{f3(r.auc_B)} {lst(r.ci_B)}",
                         "да" if r.sign_same_AB else "нет", "подтверждена" if r.confirmed else "не видно"])
    P(table(["сторона", "признак", "n", "AUC", "блоки по суткам", "по монетам", "p", "p Холм", "AUC в A", "AUC в B", "знак в A и B", "связь"], rows))
    for theme, feats in PRIMARY.items():
        P(f"\n#### {theme}: доля пробоев по корзинам (границы — квантили половины A; интервал Уилсона не учитывает кластеры монет и суток)\n")
        for s, nm in SIDES:
            for f in feats:
                t = q[(q.side == s) & (q.feature == f)]
                if t.empty:
                    continue
                rows = []
                for bi in sorted(t[t.half == "ALL"].bi.unique()):
                    a_ = t[(t.half == "ALL") & (t.bi == bi)].iloc[0]
                    ha = t[(t.half == "A") & (t.bi == bi)]
                    hb = t[(t.half == "B") & (t.bi == bi)]
                    rows.append([a_.bucket, int(a_.n), int(a_.k_o3), pc(a_.rate), ci(a_.ci_lo, a_.ci_hi), pc(a_.O1), pc(a_.O2), pc(a_.O4), pc(a_.ctrl_o3) if "ctrl_o3" in a_ and a_.bi >= 0 else "—",
                                 pp(a_.excess) if "excess" in a_ and a_.bi >= 0 else "—", f"{int(ha.iloc[0].n)} / {pc(ha.iloc[0].rate)}" if len(ha) else "—",
                                 f"{int(hb.iloc[0].n)} / {pc(hb.iloc[0].rate)}" if len(hb) else "—"])
                P(f"\n{nm}, {NAME[f]}:\n")
                P(table(["корзина", "эпизодов", "O3", "доля O3", "Уилсон 95 %", "O1", "O2", "O4", "контроль O3", "O3 − контроль", "A: n / доля", "B: n / доля"], rows))
    # ── разведка
    P("\n### Разведка (40 числовых признаков × 2 стороны; найдено на этих данных; Холм по 40 на сторону)\n")
    rows = []
    for s, nm in SIDES:
        t = auc[(auc.side == s) & (auc.group == "explore")].copy()
        t["d"] = (t.auc - 0.5).abs()
        for r in t.sort_values("d", ascending=False).head(10).itertuples():
            rows.append([nm, f"`{r.feature}`", r.n_valid, f3(r.auc), f3(r.p), f3(r.p_holm), f3(r.auc_A), f3(r.auc_B), "да" if r.sign_same_AB else "нет"])
    P(table(["сторона", "признак", "n", "AUC", "p без поправки", "p Холм (40)", "AUC в A", "AUC в B", "знак в A и B"], rows))
    # ── подход к уровню
    P("\n### Достижение расстояния до уровня за 60 мин от сигнала (U1; средние, без медиан)\n")
    rows = []
    for s, nm in SIDES:
        for dn, lab in (("0p2", "0,2 %"), ("0p1", "0,1 %"), ("0p05", "0,05 %")):
            v = res[s]["approach_dist"][dn]
            rows.append([nm, lab, v["reached"], pc(v["share"]), f"{v['mean_time_s']:.0f}" if v["reached"] else "—", f"{v['mean_mae_bp']:.1f}" if v["reached"] else "—", pc(v["o3_given_reached"]),
                         pc(v["o4_given_reached"]), pc(v["o1_given_reached"])])
    P(table(["сторона", "расстояние от уровня", "достигло", "доля эпизодов", "среднее время, с", "средний неблагоприятный ход до этого, бп", "среди достигших: O3", "O4", "O1 (до уровня не дошла)"], rows))
    P("\nT от первой сделки на уровне (только дошедшие до уровня в окне 240 мин):\n")
    rows = []
    for s, nm in SIDES:
        for k_, v in res[s]["touch_window"].items():
            rows.append([nm, k_, v["n_touched"], pc(v["break_first"]), pc(v["return_first"]), pc(v["neither"])])
    P(table(["сторона", "метка", "дошедших", "пробой раньше возврата", "возврат раньше пробоя", "ни то ни другое"], rows))
    # ── пересечения
    P("\n### Пересечения уровня без сигнала (U1, `b0`)\n")
    rows = []
    for s, nm in SIDES:
        c = res["crossings"][s]
        rows.append([nm, c["total"], c["with_signal"], c["without_signal"], pc(c["without_signal"] / c["total"]) if c["total"] else "—", json.dumps(c["without_by_open"], ensure_ascii=False),
                     json.dumps(c["with_by_open"], ensure_ascii=False)])
    P(table(["сторона", "пересечений", "со сигналом", "без сигнала", "доля без сигнала", "без сигнала: открытие минуты", "со сигналом: открытие минуты"], rows))
    # ── примеры
    P("\n### Примеры владельца\n")
    rows = []
    for e in res["examples"]:
        if not e.get("found"):
            rows.append([e["sym"], e["side"], e["gmt4"], "сигнал не найден (|Δ| > 120 с)"] + [""] * 8)
            continue
        rows.append([e["sym"], e["side"], e["gmt4"], f"Δ {e['delta_s']:+.0f} с", e.get("episode_first_gmt4", e.get("episode", "")), e.get("emp6h_bucket", ""), e.get("tpm60_bucket", ""), e.get("mv5_bucket", ""),
                     e.get("cons60_bucket", ""), e.get("pierce_ep_bucket", ""), e.get("outcome_x1_t60", ""), e.get("traded_ow_e", "")])
    P(table(["монета", "сторона", "время GMT+4", "найден", "первый сигнал эпизода", "`emp6h` корзина (№/границ)", "`tpm60`", "`mv5`", "`cons60`", "`pierce_ep`", "исход", "сделка `ow_e`"], rows))
    P("\nЗначения признаков примеров:\n")
    keys = ["emp6h", "emp2h", "tpm60", "tpm15", "mv5", "mv15", "cons60", "natr_bp", "pierce_max_bp", "spread_bp", "age_h", "pierce_ep", "spread_tau", "c5_btc_7d", "c5_eth_7d"]
    rows = [[e["sym"], e["side"]] + [("—" if e.get(k) is None else f"{e[k]:.4g}") for k in keys] for e in res["examples"] if e.get("found") and "emp6h" in e]
    P(table(["монета", "сторона"] + keys, rows))


if __name__ == "__main__":
    main()
