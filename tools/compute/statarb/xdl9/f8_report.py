#!/usr/bin/env python3
"""TK-493 w5 F (П-37 r3.2 §15, D18): черновик итогового отчёта F10 из выходов f8_agg.py. ВСЕ числа берутся из csv (агрегатор, таблицы отбора), ничего не набирается руками;
текст-обвязка - формат §15. Только stdlib.

  python f8_report.py --agg AGG --sel docs/findings/xdl9-selection-2026-11-06 --cells-params docs/research/P-37-params-cells-2026-11-06.csv --out report.md [--table1 table1.csv]
        [--label "СУХОЙ ПРОГОН НА ФОРМИРОВАНИИ (сутки 2468-2478)"] [--window "2468-2478"]

Первая строка отчёта (D18) - список «чего тест не показывает» с числами отбора: C1 торгуется 1 клеткой из 345 (кросс-биржа почти не проверена, а не «нет края»);
z-клеток отсеяно на формировании N из M; A1 в K каталогах из 31; клеток-рук и копии a1 = a0; точность правила США/не-США; плацебо-основное не построено;
одна фаза рынка, 35 суток, выжившие, нет стакана, нет проскальзывания, нет Break (В-92).
"""
import csv, os, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cell_status as CS  # noqa: E402


def rd(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def fl(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    return ("%." + str(nd) + "f") % v if v == v else "-"


def first_line(sel, cells_params):
    cp = rd(cells_params)
    traded = lambda r: r["status"].startswith("торгуется")
    z = [r for r in cp if r["kind"] in ("A1", "A2", "B1", "C1")]
    c1 = [r for r in cp if r["kind"] == "C1"]
    d2 = [r for r in cp if r["kind"] == "D2"]
    z_tr = [r for r in z if traded(r)]
    a1cat = rd(os.path.join(sel, "a1_catalogs.csv"))
    a1_sel = [r for r in a1cat if r.get("selected") not in ("", "0", None)]
    hands = 2 * len(z_tr) + sum(1 for r in d2 if traded(r))
    copies = sum(1 for r in z_tr if r.get("a1_equals_a0") == "1")
    us = rd(os.path.join(sel, "us_rule_audit40.csv"))
    us_ok = sum(1 for r in us if r.get("agree") == "1")
    parts = [
        "C1 торгуется %d клеткой из %d (кросс-биржа почти не проверена, а не «нет края»)" % (sum(1 for r in c1 if traded(r)), len(c1)),
        "%d из %d z-клеток (A1, A2, B1, C1) отсеяны на формировании, торгуется %d" % (len(z) - len(z_tr), len(z), len(z_tr)),
        "A1 отобрана в %d каталогах из %d" % (len(a1_sel), len(a1cat)),
        "%d клеток-рук (%d z-клеток × 2 руки + %d клеток D2), из них копий a1 = a0: %d" % (hands, len(z_tr), sum(1 for r in d2 if traded(r)), copies),
        "правило США/не-США верно на выборке аудита у %d из %d активов" % (us_ok, len(us)),
        "плацебо-основное не построено (есть плацебо-2)",
        "одна фаза рынка, 35 суток, выжившие, нет стакана, нет проскальзывания, нет Break (В-92)",
    ]
    return "; ".join(parts)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg", required=True)
    ap.add_argument("--sel", required=True)
    ap.add_argument("--cells-params", dest="cells_params", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--table1")
    ap.add_argument("--label", default="")
    a = vars(ap.parse_args())
    g = lambda n: os.path.join(a["agg"], n)
    summ = {r["cell"]: r for r in rd(g("cell_summary.csv"))}
    stat = {r["cell"]: r for r in rd(g("cell_status.csv"))}
    ctrl = rd(g("controls.csv"))
    msum = {r["field"]: r["value"] for r in rd(g("m_summary.csv"))}
    big = rd(g("big_trades.csv"))
    jumps = rd(g("jumps_by_exchange.csv"))
    delist = rd(g("delisted_by_exchange.csv"))
    slices = rd(g("slice_table.csv"))
    exact = rd(os.path.join(a["agg"], "d2exact", "d2_exact_cells.csv"))
    L = []
    if a["label"]:
        L.append("**%s.** Числа ниже - из выходов `%s`, а не результат торгового теста." % (a["label"], a["agg"]))
        L.append("")
    L.append("## Строка 0 (D18, §15)")
    L.append("Чего тест не показывает: " + first_line(a["sel"], a["cells_params"]) + ". " + CS.DELAY0_NOTE + ".")
    L.append("")
    # --- блок 6 строк
    kinds = Counter(r["cell"].split("|")[0] for r in stat.values())
    sc = Counter(r["status"] for r in stat.values())
    cand = [c for c, r in stat.items() if r["status"] == CS.CANDIDATE]
    tr = sum(int(r["trades"]) for r in summ.values())
    pairs = sum(int(r["pairs_traded"]) for r in summ.values())
    L.append("## Блок из 6 строк")
    L.append("1. Что считали: %d клеток-рук (%s), %d сделок, %d пар со сделкой; основной результат - закрытые сделки + MTM открытых на конце окна (D10), закрытые - рядом." % (
        len(stat), ", ".join("%s %d" % kv for kv in sorted(kinds.items())), tr, pairs))
    L.append("2. Статусы: кандидат %d, не различимо %d, не ясно %d; кандидаты: %s." % (sc.get(CS.CANDIDATE, 0), sc.get(CS.INDISTINGUISHABLE, 0), sc.get(CS.UNCLEAR, 0), ", ".join(cand) if cand else "нет"))
    L.append("3. Число испытаний: M клеток с рядом %s, без тождественных рядов %s, M_эфф (суточные ряды) %s; ожидаемое число ложных кандидатов %s ... %s." % (
        msum.get("M_cells_with_series", "-"), msum.get("M_distinct", "-"), fl(msum.get("M_eff_daily_series"), 1),
        fl(msum.get("expected_false_candidates_range_low")), fl(msum.get("expected_false_candidates_range_high"))))
    L.append("4. Условие (ж) (+1 мин на обеих ногах): net > 0 у %d из %d клеток; доля сделок длиной <= 2 мин - в таблице 1." % (
        sum(1 for r in stat.values() if r["cond_g"] == "1"), len(stat)))
    L.append("5. Назначено CEO без Судьи: см. params (строки `f8_*`, `status_*`, D5, D10-D16).")
    L.append("6. " + CS.DELAY0_NOTE)
    L.append("")
    # --- таблица 1
    t1 = []
    byc = defaultdict(dict)
    for r in ctrl:
        byc[r["cell"]][r["stage"]] = r
    for c, s in sorted(summ.items()):
        st = stat.get(c, {})
        cs = byc.get(c, {})
        t1.append([c, s["jobs"], s["pairs_traded"], s["trades"], s["net_per_trade_bps"], s["active_pair_days"],
                   s["closed_mean"], s["closed_lo90"], s["closed_hi90"], s["primary_mean"], s["primary_lo90_t"], s["primary_hi90_t"],
                   st.get("lo_boot_t", ""), st.get("skew", ""), s["net_plus1"], s["short_trade_share_le_2min"], s["open_semantics"],
                   cs.get("pl", {}).get("primary_mean", ""), cs.get("rp", {}).get("primary_mean", ""), cs.get("re", {}).get("diff_mean", ""), cs.get("re", {}).get("diff_lo90", ""),
                   st.get("mre", ""), s["open_at_end"], s["mtm_net_sum_bps"], s["worst_trade_bps"], s["p05_trade_net_bps"], s["fee_x2_mean"], s["fee_x2_lo90"],
                   s["best_base"], s["mean_without_best_base"], s["pre_window"], s["warmup_blocked_minutes"], s["trades_on_job_days_without_active"],
                   st.get("status", ""), st.get("unmet", "")])
    hdr = ["cell", "jobs", "pairs_traded", "trades", "net_per_trade_bps", "active_pair_days", "closed_mean", "closed_lo90", "closed_hi90", "primary_mean", "primary_lo90_t",
           "primary_hi90_t", "primary_lo90_boot_t", "skew", "net_plus1_min", "share_trades_le_2min", "open_semantics", "placebo2_mean", "random_pairs_mean", "diff_vs_random_entries",
           "diff_lo90", "mre", "open_at_end", "mtm_net_sum_bps", "worst_trade_bps", "p05_trade_net_bps", "fee_x2_mean", "fee_x2_lo90", "best_base", "mean_without_best_base",
           "pre_window", "warmup_blocked_minutes", "orphan_trades_job_day", "status", "unmet"]
    if a["table1"]:
        os.makedirs(os.path.dirname(os.path.abspath(a["table1"])), exist_ok=True)
        with open(a["table1"], "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(hdr)
            w.writerows(t1)
    L.append("## Таблица 1 (вид × биржа × класс × рука) - файл `%s`, строк %d" % (os.path.basename(a["table1"] or "table1.csv"), len(t1)))
    L.append("")
    L.append("| клетка | сделок | закрытые, bps/сутки/пару | закрытые + MTM (осн.) | 90 % t | +1 мин | сделок <= 2 мин | open | статус |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in t1[:40]:
        L.append("| %s | %s | %s | %s | [%s; %s] | %s | %s | %s | %s |" % (r[0], r[3], fl(r[6]), fl(r[9]), fl(r[10]), fl(r[11]), fl(r[14]), fl(r[15]), r[16], r[33]))
    if len(t1) > 40:
        L.append("| ... | еще %d строк в файле | | | | | | | |" % (len(t1) - 40))
    L.append("")
    # --- шум, скачки, большие сделки (D15)
    nbig = len(big)
    L.append("## Шум, скачки цены и крупные сделки (D15)")
    L.append("- сделок с |P&L| > 1000 bps: %d (список `big_trades.csv`); чувствительность «P&L сделки обрезан ±1000 bps» - колонки `clip1000_*` в `cell_summary.csv`: у %d клеток среднее с обрезкой отличается от основного." % (
        nbig, sum(1 for r in summ.values() if r["clip1000_mean"] and r["primary_mean"] and abs(float(r["clip1000_mean"]) - float(r["primary_mean"])) > 1e-9)))
    L.append("- минутных скачков > 40 %% на ногах TradFi: %s (по биржам и полосам - `jumps_by_exchange.csv`, %d строк); защитного правила в ядре нет." % (
        sum(int(r["jumps_gt_40pct"]) for r in jumps) if jumps else "не считалось", len(jumps)))
    L.append("")
    # --- выжившие (D16)
    L.append("## Выжившие (D16)")
    for r in delist:
        L.append("- %s: инструментов %s, со сроком снятия %s, снято в окне %s%s" % (r["exchange"], r["instruments"], r["with_delisted_ms"], r["delisted_in_window"], ("; " + r["note"]) if r["note"] else ""))
    dl = [r for r in slices if r["slice"] == "delisted_in_window"]
    L.append("- срез «снятые» с MTM: %d строк клеток (`slice_table.csv`, колонка `closed_plus_mtm_mean`)." % len(dl))
    L.append("")
    # --- D2 точный фандинг
    if exact:
        n = sum(int(r["trades"]) for r in exact)
        L.append("## Чувствительность D2 «точный двухногий фандинг» (D5 v, вне семьи)")
        L.append("- сделок D2: %d; Σ разности (точный − ядро) %s bps; доля сделок с |разность| > 1 bps в клетках: от %s до %s; 99-й процентиль |разности| по клеткам: максимум %s bps." % (
            n, fl(sum(float(r["sum_diff_bps"]) for r in exact)), fl(min(float(r["share_abs_diff_gt_1bps"]) for r in exact), 3), fl(max(float(r["share_abs_diff_gt_1bps"]) for r in exact), 3),
            fl(max(float(r["p99_abs_diff_bps"]) for r in exact))))
        L.append("")
    # --- вид сетки D2
    gs = [r for r in slices if r["group"] == "d2_grid"]
    if gs:
        byk = defaultdict(lambda: [0, 0.0])
        for r in gs:
            byk[r["slice"]][0] += int(r["trades"] or 0)
            byk[r["slice"]][1] += float(r["net_sum_bps"] or 0)
        L.append("## D2: вид сетки выплат (L3-01, D5 i)")
        for k, v in sorted(byk.items()):
            L.append("- %s: сделок %d, Σ net %s bps" % (k, v[0], fl(v[1])))
        L.append("")
    L.append("## Оговорки")
    L.append("- Исполнимость: минутный спред по закрытию свечи не равен исполнимому; глубины стакана нет; delay-0 - «цена последней сделки», не граница исполнимого (D11).")
    L.append("- Все решения методики - «назначено CEO без Судьи, подтвердить» (params).")
    L.append("- Числа отчёта = числа csv: каждое число этого текста выведено скриптом `f8_report.py` из файлов каталога `%s`." % a["agg"])
    with open(a["out"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    print("report: %s (%d строк), таблица 1: %d строк" % (a["out"], len(L), len(t1)))


if __name__ == "__main__":
    main()
