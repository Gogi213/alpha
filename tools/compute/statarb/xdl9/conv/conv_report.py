#!/usr/bin/env python3
"""TK-493, П-38 r2: сборка таблиц отчёта калибровки из выходов conv_run / conv_k7 / conv_k8 / conv_hslice / conv_k1b / conv_size (числа отчёта = числа csv/json).

Пишет <dest>/tables.md (разделы с таблицами) и копирует csv-пакет в <dest>/bundle/. Ничего не считает заново, кроме долей Q5 по терцилям плотности (из q5_detail и меты пар).
  python conv_report.py --out OUT --dest DEST --L-full 1440 --L-train 1440
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import shutil
import sys

import numpy as np

CLS_RU = {"crypto": "крипта", "eq": "акции+ETF"}


def f(x, nd=1, pct=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    if pct:
        return "%.*f %%" % (nd, 100.0 * x)
    if isinstance(x, bool):
        return "да" if x else "нет"
    if isinstance(x, int):
        return str(x)
    return "%.*f" % (nd, x)


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def jl(p):
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def rcsv(p):
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.isfile(p) else []


def num(x):
    return None if x in (None, "", "None") else float(x)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--L-full", type=int, default=1440, dest="Lf")
    ap.add_argument("--L-train", type=int, default=1440, dest="Lt")
    ap.add_argument("--classes", default="crypto,eq")
    a = ap.parse_args(argv)
    O, D = a.out, a.dest
    os.makedirs(os.path.join(D, "bundle"), exist_ok=True)
    classes = a.classes.split(",")
    md = []
    # ---- K0, K1
    k0 = jl(os.path.expanduser("~/xdl9/c2/calib_start.json"))
    uni = jl(os.path.join(O, "k7", "universe.json"))
    md.append("## T1. K0 и универсум (K1)\n")
    if k0:
        md.append("K0 `calib_start.json`: время UTC %s; `gate_ready` существует: %s; валиден как манифест (§3.4): %s; проверки в манифесте: %s; sha256 маркера `%s…`; sha256 params `%s…`.\n" % (
            k0["time_utc"], f(k0["gate_ready_exists"]), f(k0["gate_ready_valid"]), ",".join(k0["gate_checks"]) or "нет", k0["gate_sha256"][:12], k0["params_sha256"][0][:12]))
    if uni:
        rows = [[k.split("|")[0], k.split("|")[1], v["pairs"], v["cells"], v["assets"], "да" if k.split("|")[1] in ("24x7", "US", "HK") and k.split("|")[0] in ("crypto", "eq") else "нет"]
                for k, v in sorted(uni["pairs_by_class_market"].items())]
        md.append(table(["класс", "рынок", "пар (c1d2 = 1)", "ячеек", "активов", "в основном ряду"], rows))
        rows = [[ex, v["legs"], f(v["valid_share_median"], 3), f(v["valid_share_ge50"], 1, True), f(v["chain_share_median"], 3)] for ex, v in uni["open_semantics_by_exchange"].items()]
        md.append("Семантика `open` и заполненность по биржам (ноги основного ряда, формирование): доля минут `open[t] = close[t−1]` среди минут с обеими ценами; «chain» ≈ 1 — цепочка, ≈ 0 — первая сделка.\n")
        md.append(table(["биржа", "ног", "доля минут со сделкой (медиана)", "ног с долей ≥ 50 %", "open[t] = close[t−1] (медиана)"], rows))
        rows = [[CLS_RU.get(c, c), v["pairs"], v["floor_defined"], f(v["valid_share_median"], 3), f(v["valid_share_q10"], 3), f(v["mad0_share_mean"], 3)] for c, v in uni["main_by_class"].items()]
        md.append(table(["класс", "пар", "тик определён (Q4)", "доля общих валидных минут, медиана", "10-й процентиль", "доля минут σ = пол"], rows))
    # ---- Ролл по биржам (K9)
    mdir_r = os.path.join(O, "calib", "cache", "L%d" % a.Lf, "meta")
    roll, tot = {}, {}
    for cls in classes:
        for pid in (jl(os.path.join(O, "calib", "grid_full", cls, "pids.json")) or []):
            m = jl(os.path.join(mdir_r, pid + ".json"))
            if not m:
                continue
            exA, rest = pid.split("~", 1)
            exB = rest.split("|")[0]
            for ex, v in ((exA, m["roll_full"][0]), (exB, m["roll_full"][1])):
                tot[ex] = tot.get(ex, 0) + 1
                if v is not None:
                    roll.setdefault(ex, []).append(v)
    if tot:
        md.append("### Оценка Ролла полуспреда на ногу по биржам (K9; формирование, Pi_full; нет отрицательной ковариации — оценки нет)" + chr(10))
        rows = [[ex, tot[ex], len(roll.get(ex, [])), f(len(roll.get(ex, [])) / tot[ex], 1, True)] + ([f(float(np.quantile(roll[ex], q)), 2) for q in (0.25, 0.5, 0.75)] if roll.get(ex) else ["—", "—", "—"]) for ex in sorted(tot)]
        md.append(table(["биржа", "ног-вхождений в пары", "с оценкой", "доля", "a q25, bps", "медиана", "q75"], rows))
    # ---- K2
    k2c, k2t = jl(os.path.join(O, "k2_calib.json")), jl(os.path.join(O, "k2_train.json"))
    md.append("## T2. K2: выбор окна базиса L (правило R-L)\n")
    rows = []
    for nm, k2 in (("Pi_full (2468…2478)", k2c), ("Pi_train (2468…2471)", k2t)):
        if k2:
            for cls in ("crypto", "eq"):
                v = k2["by_class"].get(cls)
                if v:
                    rows.append([nm, CLS_RU[cls], v["pairs_in"], v["pairs_total"], f(v["q25"], 3), f(v["median"], 3), f(v["q75"], 3), f(v["share_below_1"], 1, True), k2["L"]])
    md.append(table(["конвейер", "класс", "пар в оценке", "пар в классе", "q25 отношения", "медиана", "q75", "доля пар с отношением < 1", "выбранное L"], rows))
    # ---- структурные числа
    md.append("## T3. Структурные числа класса (K4), Pi_train и Pi_full\n")
    strs = {"train": jl(os.path.join(O, "train", "structural_train.json")), "full": jl(os.path.join(O, "calib", "structural_full.json"))}
    rows = []
    for cls in classes:
        for tg in ("train", "full"):
            s = (strs[tg] or {}).get(cls)
            if not s:
                continue
            g = s.get("gmax_rule", {})
            rows.append([CLS_RU[cls], "Pi_" + tg, s.get("pairs"), s.get("k_stale"), s.get("m_r"), f(s.get("q_stale"), 3), f(s.get("q_c"), 2), f(s.get("m_shift"), 3), f(g.get("gmax"), 1),
                         g.get("gmax_kind") or "—", f(g.get("gsig_max"), 1), f(g.get("t_struct"), 0) + (" (обрезан)" if g.get("t_struct_truncated") else ""), g.get("episodes_total")])
    md.append(table(["класс", "конвейер", "пар", "K_stale, мин", "m_r", "q_stale", "q_c, bps", "m (сдвиг базиса), σ", "G_max, bps", "вид G_max", "Gσ_max", "T_struct, мин", "эпизодов (θ=25,2; k=3; p=1)"], rows))
    for cls in classes:
        for tg in ("full",):
            s = (strs[tg] or {}).get(cls)
            if not s:
                continue
            g = s["gmax_rule"]
            md.append("### Корзины гонки «схождение или стоп», %s, Pi_%s (по g_in в bps; независимые единицы = актив × сутки)\n" % (CLS_RU[cls], tg))
            rows = []
            for b in g["bins"]:
                if b["basis"] != "bps":
                    continue
                a90 = None
                for e, v in zip(g.get("a90_edges") or [], g.get("a90_vals") or []):
                    if abs(e - b["lo"]) < 1e-9:
                        a90 = v
                rows.append(["%g–%s" % (b["lo"], "∞" if b["hi"] is None else "%g" % b["hi"]), b["episodes"], b["units"], f(b["support"]),
                             ("%s [%s; %s]" % (f(b.get("p_conv"), 3), f(b.get("p_conv_lo"), 3), f(b.get("p_conv_hi"), 3))) if b.get("support") else "опоры нет",
                             ("%s [%s; %s]" % (f(b.get("p_stop"), 3), f(b.get("p_stop_lo"), 3), f(b.get("p_stop_hi"), 3))) if b.get("support") else "опоры нет",
                             f(b.get("race_lost")) if b.get("support") else "—", f(a90, 2)])
            md.append(table(["корзина g_in, bps", "эпизодов", "единиц", "опора ≥ 20", "P(схождение) [90 %]", "P(стоп 2·g_in) [90 %]", "гонка проиграна", "A90, bps"], rows))
            rows = []
            for b in g["bins"]:
                if b["basis"] == "sigma" and b.get("support"):
                    rows.append(["%g–%s" % (b["lo"], "∞" if b["hi"] is None else "%g" % b["hi"]), b["episodes"], b["units"], f(b.get("p_conv"), 3), f(b.get("p_stop"), 3), f(b.get("race_lost"))])
            if rows:
                md.append("Корзины по g_in/σ (только с опорой):\n")
                md.append(table(["корзина g_in/σ", "эпизодов", "единиц", "P(схождение)", "P(стоп)", "гонка проиграна"], rows))
            k6 = g.get("k6_stop_was_right") or []
            if k6:
                md.append("K6 «был ли стоп прав» (эпизоды, закончившиеся тест-стопом 2·g_in): доля, сошедшаяся бы за T после стопа, и дальнейший экстремум в долях g_in.\n")
                md.append(table(["T после стопа, мин", "стопов", "сошлись бы", "экстремум q50", "q90", "q99"], [[x["T"], x["stops"], f(x["share_converged"], 3), f(x["ext_q50"], 2), f(x["ext_q90"], 2), f(x["ext_q99"], 2)] for x in k6]))
    # ---- Q5
    md.append("## T4. Допуск пар по полураспаду (Q5), доли и терцили плотности\n")
    q5d = jl(os.path.join(O, "calib", "q5_detail_full.json")) or {}
    mdir = os.path.join(O, "calib", "cache", "L%d" % a.Lf, "meta")
    rows = []
    for cls in classes:
        pids = jl(os.path.join(O, "calib", "grid_full", cls, "pids.json")) or []
        cl = []
        for pid in pids:
            fp = os.path.join(mdir, pid + ".json")
            if pid in q5d and os.path.isfile(fp):
                m = json.load(open(fp, encoding="utf-8"))
                cl.append((pid, m["n_valid"]))
        if not cl:
            continue
        nv = np.array([x[1] for x in cl], float)
        q1, q2 = np.quantile(nv, [1 / 3, 2 / 3])
        for lab, mask in (("все", np.ones(len(cl), bool)), ("плотность: нижний терциль", nv <= q1), ("средний", (nv > q1) & (nv <= q2)), ("верхний", nv > q2)):
            ids = [cl[i][0] for i in np.flatnonzero(mask)]
            rr = [q5d[i] for i in ids]
            def sh(T):
                return float(np.mean([x.get("hl_hi") is not None and x.get("phi15") is not None and 0 < x["phi15"] < 1 and x["hl_hi"] <= T / 4.0 for x in rr])) if rr else None
            rows.append([CLS_RU[cls], lab, len(ids), f(float(np.mean([x.get("phi15") is not None and 0 < x["phi15"] < 1 for x in rr])), 1, True) if rr else "—",
                         f(sh(120), 1, True), f(sh(360), 1, True), f(sh(1440), 1, True)])
    md.append("Доля пар с `φ₁₅ ∈ (0,1)` и верхней границей `HL₁₅ ≤ T/4` (Pi_full). Если доля ≈ 100 % или монотонна по плотности — Q5 фильтром не считается (§7.2).\n")
    md.append(table(["класс", "группа", "пар", "φ₁₅ ∈ (0,1)", "допуск T = 2 ч", "T = 6 ч", "T = 24 ч"], rows))
    # ---- сетка
    for cls in classes:
        rowsg = rcsv(os.path.join(O, "k7", "grid_class_%s.csv" % cls))
        if not rowsg:
            continue
        md.append("## T5-%s. Сетка 180 комбинаций, класс «%s», Pi_full (весь класс; +1 мин; издержки 12,6 bps)\n" % (cls, CLS_RU[cls]))
        md.append("Колонки: сделок (после дедупликации), суток со сделками, активов, валовая и чистая на сделку (bps, +1 мин), фандинг на сделку, доля сделок ≤ 2 мин, доля отложенных входов, `y_d` = среднее по суткам на пару-сутки (bps) и 90 %-границы блочного t, `n/n_эфф/df`, «ок» — выполнены `n_min`, `k_days`, ≥ 5 активов. T — срок сценария, ч.\n")
        rows = []
        for r in rowsg:
            rows.append([r["theta"], r["k"], r["p"], "%g" % (float(r["T"]) / 60), "2" if r["s_stop"] in ("2", "2.0") else "только S1", r["trades"], r["days"], r["assets"], f(num(r["gross_trade"]), 2), f(num(r["mean_trade"]), 2),
                         f(num(r["fund_trade"]), 2), f(num(r["share_le2"]), 1, True) if r["share_le2"] not in ("", "None") else "—", f(num(r["share_def"]), 1, True) if r["share_def"] not in ("", "None") else "—",
                         f(num(r["mean"]), 2), "[%s; %s]" % (f(num(r["lo"]), 2), f(num(r["hi"]), 2)), "%s/%s/%s" % (r["n_days"], r["n_eff"], r["df"]), "да" if r["ok"] == "True" else "нет"])
        md.append(table(["θ", "k", "p", "T, ч", "S0", "сделок", "суток", "активов", "валовая", "чистая", "фандинг", "≤ 2 мин", "отлож.", "y_d", "90 % t", "n/n_эфф/df", "ок"], rows))
        bundle_copy = [("k7", "grid_class_%s.csv" % cls), ("k7", "grid_cells_%s.csv" % cls), ("k7", "cells_%s.csv" % cls)]
        for sub, fn in bundle_copy:
            if os.path.isfile(os.path.join(O, sub, fn)):
                shutil.copy(os.path.join(O, sub, fn), os.path.join(D, "bundle", fn))
    # ---- выбор и формирование
    md.append("## T6. Выбор на уровне класса, условия формирования (§7.4)\n")
    rows = []
    summ = jl(os.path.join(O, "k7", "summary.json")) or {}
    for cls in classes:
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls))
        if not sel:
            continue
        ad = (sel.get("a3b_detail") or {}).get("detail", {})
        rows.append([CLS_RU[cls], str(sel.get("c_full")), str(sel.get("c_train")), f(sel.get("plateau")), f(sel.get("agree")),
                     "H0→H1: %s (y_d %s), H1→H0: %s (y_d %s)" % (f((sel.get("a3b") or [None, None])[0]), f((ad.get("H0") or {}).get("mean"), 2), f((sel.get("a3b") or [None, None])[1]), f((ad.get("H1") or {}).get("mean"), 2)),
                     ", ".join("%s:%s" % (k, "+" if v else "−") for k, v in sorted((sel.get("a8") or {}).items())), sel.get("distinct_behaviours"), f(sel.get("formed")), "; ".join(sel.get("reasons") or []) or "—"])
    md.append(table(["класс", "c_полн (θ, k, p, T мин, S0)", "c_обуч", "плато", "согласие", "A3b (половины активов)", "A8 (без биржи X: + среднее y_d > 0)", "различных поведений из 180", "класс сформирован", "причины отказа"], rows))
    md.append("### K8 и множественность (формирование)\n")
    rows = []
    for cls in classes:
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls))
        k8 = jl(os.path.join(O, "k7", "k8_shift_%s.json" % cls))
        if not sel:
            continue
        pw = sel.get("p_win") or {}
        sh = (k8 or {}).get("shifts", {})
        rows.append([CLS_RU[cls], f(sel.get("k8_selected_mean_yd"), 3), f(sel.get("k8_reversed_mean_yd"), 3), f(pw.get("obs_best"), 3), f(pw.get("p_win"), 3), f(sel.get("grid_positive_mean_share"), 1, True),
                     "; ".join("%s: сделок %s из %s, y_d %s" % (k, v["trades_done"], (k8 or {}).get("real_closed_trades"), f(v["mean_yd"], 3)) for k, v in sorted(sh.items(), key=lambda kv: int(kv[0])))])
    md.append(table(["класс", "y_d выбранной комбинации (все пары)", "y_d при перевёрнутом направлении (K8 а)", "максимум LCB по сетке", "p_win (K8 в)", "доля комбинаций с y_d > 0", "перенос минуты входа (K8 б)"], rows))
    # ---- вариант без Q5 (чувствительность к условию §7.2)
    N5 = os.path.join(os.path.dirname(O.rstrip("/")), "out_noq5")
    if os.path.isdir(os.path.join(N5, "k7")):
        md.append("### T6b. Вариант «Q5 выключен» (чувствительность: доля Q5 монотонна по плотности, §7.2); те же числа класса, те же правила выбора и допуска" + chr(10))
        rows = []
        for cls in classes:
            sel = jl(os.path.join(N5, "k7", "selection_%s.json" % cls))
            if not sel:
                continue
            cr = rcsv(os.path.join(N5, "k7", "cells_%s.csv" % cls))
            cnt = [sum(1 for r in cr if r["A%d" % i] == "1") for i in range(1, 8)]
            ad = (sel.get("a3b_detail") or {}).get("detail", {})
            rows.append([CLS_RU[cls], str(sel.get("c_full")), str(sel.get("c_train")), f(sel.get("plateau")), f(sel.get("agree")),
                         "H0→H1: %s (y_d %s, %s сд.), H1→H0: %s (y_d %s, %s сд.)" % (f((sel.get("a3b") or [None, None])[0]), f((ad.get("H0") or {}).get("mean"), 3), (ad.get("H0") or {}).get("trades"), f((sel.get("a3b") or [None, None])[1]),
                                                                                    f((ad.get("H1") or {}).get("mean"), 3), (ad.get("H1") or {}).get("trades")),
                         ", ".join("%s:%s" % (k, "+" if v else "−") for k, v in sorted((sel.get("a8") or {}).items())), f(sel.get("formed")), "; ".join(sel.get("reasons") or []) or "—",
                         "/".join(str(c) for c in cnt), len(sel.get("cells_admitted", []))])
            for fn in ("grid_class_%s.csv" % cls, "cells_%s.csv" % cls):
                if os.path.isfile(os.path.join(N5, "k7", fn)):
                    shutil.copy(os.path.join(N5, "k7", fn), os.path.join(D, "bundle", fn.replace(".csv", "_noq5.csv")))
        md.append(table(["класс", "c_полн", "c_обуч", "плато", "согласие", "A3b", "A8", "класс сформирован", "причины отказа", "клеток, прошедших A1/…/A7 (из 28)", "допущено клеток"], rows))
        for cls in classes:
            sel = jl(os.path.join(N5, "k7", "selection_%s.json" % cls))
            if sel and sel.get("c_full"):
                pw = sel.get("p_win") or {}
                txt = "%s, вариант без Q5: y_d выбранной комбинации %s, при перевёрнутом направлении %s; максимум LCB по сетке %s; p_win %s; доля комбинаций с y_d > 0 %s; сделок %s, доля побед %s, различных поведений %s." % (
                    CLS_RU[cls], f(sel.get("k8_selected_mean_yd"), 3), f(sel.get("k8_reversed_mean_yd"), 3), f(pw.get("obs_best"), 3), f(pw.get("p_win"), 3), f(sel.get("grid_positive_mean_share"), 1, True),
                    f((sel.get("tail_selected") or {}).get("trades"), 0), f((sel.get("tail_selected") or {}).get("win_rate"), 3), sel.get("distinct_behaviours"))
                md.append(txt + chr(10))
    # ---- чувствительность пола оборота Q3 (K9)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import conv_agg as AG
        rows = []
        for cls in classes:
            for lab, tag in (("x0 (пола нет)", "sens_f0_%s" % cls), ("x1 (1 000, основной)", "k8_%s" % cls), ("x10 (10 000)", "sens_f10000_%s" % cls)):
                d = os.path.join(O, "calib", "grid_" + tag)
                if not os.path.isdir(os.path.join(d, cls)):
                    continue
                g = AG.Grid(d, cls)
                allr = np.arange(len(g.pids))
                ser = AG.y_series(g, 0, allr)["primary"]
                tn = g.tab[0][allr]
                n = float(tn[:, :, 0 + AG.FI["n"]].sum())
                rows.append([CLS_RU[cls], lab, str(list(g.combos[0])), int(n), f(float(tn[:, :, AG.FI["gross"]].sum()) / n, 2) if n else "—", f(float(tn[:, :, AG.FI["net"]].sum()) / n, 2) if n else "—", f(sum(ser) / len(ser), 4) if ser else "—"])
        if rows:
            md.append("### Чувствительность пола оборота Q3 для выбранной комбинации класса (K9; числа класса заморожены, формирование)" + chr(10))
            md.append(table(["класс", "пол оборота", "комбинация", "сделок", "валовая на сделку", "чистая на сделку", "y_d"], rows))
    except Exception as e:      # noqa: BLE001
        md.append("Чувствительность пола оборота не построена: %s" % e + chr(10))
    # ---- ячейки
    md.append("## T7. Ячейки (биржевые пары × класс): параметры класса, допуск A1–A7, лучшая точка сетки\n")
    for cls in classes:
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls))
        cr = {r["cell"]: r for r in rcsv(os.path.join(O, "k7", "cells_%s.csv" % cls))}
        if not sel:
            continue
        best = sel.get("cells_best_point", {})
        nob = set(sel.get("cells_no_breakeven", []))
        md.append("### %s: параметры класса %s; сформирован: %s\n" % (CLS_RU[cls], sel.get("c_full"), f(sel.get("formed"))))
        rows = []
        for c in sorted(cr):
            r = cr[c]
            b = best.get(c)
            rows.append([c.replace("/" + cls, ""), r["pairs"], r["trades"], r["assets"], r["days"], f(num(r["mean"]), 2), f(num(r["lo_t"]), 2), "".join("+" if r["A%d" % i] == "1" else "−" for i in range(1, 8)),
                         "торгуется" if r["tradable"] == "1" else "нет", ("θ %g k %s p %s T %g ч %s: %s сд., чист. %s" % (b["theta"], b["k"], b["p"], b["T"] / 60, "S0" if b["s_stop"] else "S1", b["trades"], f(b["net_trade"], 1))) if b else "нет точки с ≥ 20 сделок",
                         "нет безубыточной точки" if c in nob else "есть"])
        md.append(table(["ячейка", "пар", "сделок", "активов", "суток", "y_d", "нижняя t", "A1–A7", "статус", "лучшая точка сетки по чистой на сделку (≥ 20 сд.)", "безубыточная точка"], rows))
    # ---- K1c
    md.append("## T8. K1c: ожидаемое число сделок при +1 мин по ячейке и θ (k = 3, p = 1, T = 6 ч, S0 = 2), до выбора\n")
    for cls in classes:
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls))
        if not sel:
            continue
        rows = [[c.replace("/" + cls, ""), v["pairs"]] + [v.get("theta_%s" % t) for t in ("25.2", "40.0", "80.0", "160.0", "320.0")] + [f(v["power_below_A1"])] for c, v in sorted(sel["k1c"].items())]
        md.append("**%s**\n" % CLS_RU[cls])
        md.append(table(["ячейка", "пар", "θ=25,2", "40", "80", "160", "320", "мощность ниже A1"], rows))
    # ---- подсказки
    hs = jl(os.path.join(O, "k7", "hints_c2_summary.json"))
    if hs:
        md.append("## T9. Подсказки видео как срезы формирования (описательно)\n")
        rows = []
        for h in hs:
            asel = h.get("at_selected") or {}
            ref = {tuple(r["combo"])[0]: r for r in h.get("ref_points", [])}
            rows.append([h["name"], h["verdict"], "да" if h["in_universe"] else "нет", h.get("market") or "—", "да" if h.get("in_main_series") else "нет", f(h.get("valid_share"), 3), f(h.get("dev_abs_max_bps"), 0), f(h.get("sigma_rob_median_bps"), 1),
                         f(h.get("hl15_hi"), 0), "/".join(f(h.get("q5_T%d" % t)) for t in (120, 360, 1440)) if h.get("in_main_series") else "—",
                         ("%s сд., чист. %s" % (asel.get("trades"), f(asel.get("net"), 1))) if asel else "—",
                         " ".join("%s:%s/%s" % (("%g" % k), v.get("trades"), f(v.get("net"), 1)) for k, v in sorted(ref.items())) if ref else "—"])
        md.append(table(["связка", "вердикт R2'", "в универсуме C2", "рынок", "в основном ряду", "доля валидных минут", "макс. |отклонение|, bps", "σ_rob (медиана), bps", "HL₁₅ верх, мин", "Q5: 2/6/24 ч", "на комбинации класса", "θ=25,2/40/80/160/320 (k 3, p 1, T 6 ч, S0): сделок/чист. на сделку"], rows))
        w2 = rcsv(os.path.join(O, "k7", "hints_w2_formation.csv"))
        if w2:
            md.append("Правило «как в видео» (W2), формирование, база 1440, таймаут 24 ч, вход на закрытии минуты пересечения; заполнение +1 мин; net тейкер 12,6 / мейкер 5,04. `var`: rel — отклонение от скользящей базы, abs — от нуля (после снятия десятичного сдвига k для крипты).\n")
            rows = [[r["name"], r["var"], r["theta"], r["n_ep"], "%s/%s/%s" % (r["n_ep_1min"], r["n_ep_2min"], r["n_ep_3p"]), r["trades"], f(num(r["gross0_mean"]), 2), r["trades_p1"], f(num(r["gross_p1_mean"]), 2), f(num(r["net_taker_p1_mean"]), 2),
                     f(num(r["net_maker_p1_mean"]), 2)] for r in w2]
            md.append(table(["связка", "var", "θ, bps", "эпизодов", "длина 1/2/3+ мин", "сделок", "валовая (delay 0)", "сделок +1", "валовая +1 мин", "net тейкер +1", "net мейкер +1"], rows))
            for fn in ("hints_w2_formation.csv", "hints_c2_grid.csv"):
                if os.path.isfile(os.path.join(O, "k7", fn)):
                    shutil.copy(os.path.join(O, "k7", fn), os.path.join(D, "bundle", fn))
    # ---- K1b
    k1b = jl(os.path.join(O, "k7", "k1b.json"))
    if k1b:
        md.append("## T10. K1b: пары вне универсума (c1d2 = 0, крипта) с отклонением ≥ 160 / 320 bps\n")
        th = k1b["thetas"]
        agg = {}
        for r in k1b["by_verdict_why"]:
            w = r["why"] or "—"
            cat = "нога - дубль / другая котировка" if w.startswith("нога") else ("—" if w == "—" else "прочее")
            a_ = agg.setdefault((r["verdict"], cat), {})
            for kk, vv in r.items():
                if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                    a_[kk] = a_.get(kk, 0) + vv
        rows = [[k_[0], k_[1], int(v.get("pairs_with_panels", 0)), int(v.get("pairs_evaluated", 0))] + sum(([int(v.get("pairs_with_ep_%g" % t, 0)), int(v.get("episodes_%g" % t, 0)), int(v.get("pairs_with_ep_gt2min_%g" % t, 0))] for t in th), []) for k_, v in sorted(agg.items())]
        head = ["вердикт R2'", "причина", "пар с панелями", "оценено"] + sum((["пар с эпизодами ≥ %g" % t, "эпизодов", "пар с эпизодом > 2 мин"] for t in th), [])
        md.append("Строк `asset_pairs.csv` с c1d2 = 0 (крипта, между биржами): %d. Универсум не менялся." % k1b["rows_total"] + chr(10))
        md.append(table(head, rows))
        shutil.copy(os.path.join(O, "k7", "k1b.json"), os.path.join(D, "bundle", "k1b.json"))
    # ---- G-C2-8
    g8 = jl(os.path.join(O, "size", "g_c2_8.json"))
    if g8:
        md.append("## T11. G-C2-8: размер правила A2 на синтетическом нуле (seed 37, B = 3000)\n")
        md.append(table(["сценарий нуля", "n", "блок", "n_эфф", "повторов", "нижняя t > 0", "нижняя бутстреп-t > 0", "A2 (обе)"],
                        [[r["scen"], r["n"], r["block"], r["n_eff"], r["reps"], f(r["p_t"], 3), f(r["p_boot_t"], 3), f(r["p_a2"], 3)] for r in g8["single"]]))
        md.append("Победитель из K независимых нулевых рядов (максимальная нижняя граница блочного t), проверка A2 для победителя:\n")
        md.append(table(["сценарий", "n", "K", "повторов", "нижняя t победителя > 0", "A2 победителя"], [[r["scen"], r["n"], r["K"], r["reps"], f(r["p_winner_t_lower_gt0"], 3), f(r["p_winner_a2"], 3)] for r in g8["winner"]]))
        shutil.copy(os.path.join(O, "size", "g_c2_8.json"), os.path.join(D, "bundle", "g_c2_8.json"))
    # ---- счётчики
    md.append("## T12. Счётчики выбранных комбинаций и хвост (разрывы формирования)\n")
    for cls in classes:
        sel = jl(os.path.join(O, "k7", "selection_%s.json" % cls))
        if not sel or not sel.get("counters_selected"):
            continue
        cn = sel["counters_selected"]
        md.append("**%s**, комбинация %s: %s\n" % (CLS_RU[cls], sel.get("c_full"), ", ".join("%s=%s" % (k, v) for k, v in sorted(cn.items()))))
        tl = sel.get("tail_selected") or {}
        md.append("Хвост: сделок %s, доля побед %s, выигрыш/проигрыш %s, среднее %s; квартили «каток» (по сетке): доля побед q75 = %s, отношение q25 = %s.\n" % (f(tl.get("trades"), 0), f(tl.get("win_rate"), 3), f(tl.get("win_loss_ratio"), 3), f(tl.get("mean"), 2),
                                                                                                            f((sel.get("katok_bounds") or {}).get("win_rate_q75"), 3), f((sel.get("katok_bounds") or {}).get("ratio_q25"), 3)))
    md.append("## T13. Множественность (формирование)\n")
    al = summ.get("_all", {})
    md.append("M (допущенных клеток) = %s; M_эфф (оценка по суточным y_d формирования, не по окну; (Σλ)²/Σλ²) = %s; M_сетка = 180 × %s классов = %s; классов сформировано: %s.\n" % (
        al.get("M"), f(al.get("M_eff_formation_estimate"), 2), al.get("classes"), al.get("M_grid"), sum(1 for c in classes if summ.get(c, {}).get("formed"))))
    open(os.path.join(D, "tables.md"), "w", encoding="utf-8", newline="\n").write("\n".join(md))
    with open(os.path.join(D, "bundle", "SHA256.txt"), "w", encoding="utf-8", newline="\n") as fh:
        for p in sorted(glob.glob(os.path.join(D, "bundle", "*"))):
            if os.path.basename(p) != "SHA256.txt":
                fh.write("%s  %s\n" % (sha(p), os.path.basename(p)))
    print("tables.md: %d символов" % sum(len(x) for x in md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
