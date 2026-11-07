#!/usr/bin/env python3
"""TK-493, П-38 r2 §12.1 (G0 часть 2): сборка addendum params `P-38-params-calib-<дата>.csv` и каталога списков из выходов калибровки.

Строки: key,value,status,when,source (как у params G0 часть 1). Большие списки (пары, Q5, клетки, сетка) лежат файлами в каталоге списков; в addendum - путь и sha256.
Статусы: «измерено по правилу …» - значение получено записанным правилом на формировании; «назначено CEO без Судьи, подтвердить» - прочтение реализации, не определённое протоколом однозначно.
  python conv_addendum.py --out OUT --dest DEST --prefix docs/research/P-38-calib-2026-11-07 --when "07.11 G0 ч.2" --code-dir CODE
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

CLS = ("crypto", "eq")
RU = {"crypto": "крипта", "eq": "акции+ETF"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def jl(p):
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def rcsv(p):
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.isfile(p) else []


def jv(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda o: None if (isinstance(o, float) and not math.isfinite(o)) else str(o))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--when", default="07.11 G0 ч.2")
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--csv-name", default="P-38-params-calib-2026-11-07.csv")
    ap.add_argument("--L-full", type=int, default=1440, dest="Lf")
    ap.add_argument("--L-train", type=int, default=1440, dest="Lt")
    a = ap.parse_args(argv)
    O = a.out
    ld = os.path.join(a.dest, os.path.basename(a.prefix))
    os.makedirs(ld, exist_ok=True)
    MEAS, ASSIGN = "измерено по правилу протокола на формировании (сутки 2465…2478)", "назначено CEO без Судьи, подтвердить"
    rows = []

    def add(key, value, status, source):
        rows.append([key, value, status, a.when, source])

    def put(src, name):
        shutil.copy(src, os.path.join(ld, name))
        return "%s/%s" % (a.prefix, name), sha(os.path.join(ld, name))
    P = "docs/research/P-38-convergence-xdl9.md"
    add("params_calib_state", "G0 часть 2: результаты калибровки на формировании; окно (сутки >= 2479) не читалось; значения ниже получены кодом conv_*.py (sha256 - строки code_sha256_*) по правилам протокола, подгонки по результату нет", ASSIGN, P + " §12.1")
    # K0
    k0 = os.path.expanduser("~/xdl9/c2/calib_start.json")
    pth, h = put(k0, "calib_start.json")
    c0 = jl(k0)
    add("calib_start_json", "%s; gate_ready существует: %s; валиден как манифест §3.4: %s; Gate в универсум C2 НЕ включён (нет манифеста проверок; пар Gate по R2' и единиц оборота Gate в коде нет); результат без Gate = основной; Gate - отдельным дополнением" % (
        c0["time_utc"], c0["gate_ready_exists"], c0["gate_ready_valid"]), MEAS, "%s sha256=%s" % (pth, h))
    # K2, L
    for nm in ("k2_calib", "k2_train"):
        if os.path.isfile(os.path.join(O, nm + ".json")):
            put(os.path.join(O, nm + ".json"), nm + ".json")
    k2c, k2t = jl(os.path.join(O, "k2_calib.json")), jl(os.path.join(O, "k2_train.json"))
    add("basis_window_L", jv({"Pi_full": a.Lf, "Pi_train": a.Lt, "k2_full": k2c["by_class"] if k2c else None, "k2_train": k2t["by_class"] if k2t else None}), MEAS, P + " §7.1 K2 (R-L); k2_calib.json, k2_train.json")
    # структурные числа
    for nm, sub in (("structural_full", "calib"), ("structural_train", "train"), ("classpar_full", "calib"), ("classpar_train", "train"), ("classpar_H0", "calib"), ("classpar_H1", "calib")):
        fp = os.path.join(O, sub, nm + ".json")
        if os.path.isfile(fp):
            pth, h = put(fp, nm + ".json")
            add("file_" + nm, pth, MEAS, "sha256=" + h)
    sf, st = jl(os.path.join(O, "calib", "structural_full.json")) or {}, jl(os.path.join(O, "train", "structural_train.json")) or {}
    cf, ct = jl(os.path.join(O, "calib", "classpar_full.json")) or {}, jl(os.path.join(O, "train", "classpar_train.json")) or {}
    for key, fld, src in (("q_stale_by_class", "q_stale", P + " §4.2, §7.1 K4"), ("shift_m_by_class", "m_shift", P + " §5.4"), ("q_c_by_class", "q_c", P + " §5.5"), ("k_stale_by_class", "kstale", P + " §5.5"),
                          ("m_r_by_class", "m_r", P + " §6")):
        add(key, jv({"Pi_full": {c: cf.get(c, {}).get(fld) for c in CLS}, "Pi_train": {c: ct.get(c, {}).get(fld) for c in CLS}}), MEAS, src)
    add("gmax_by_class", jv({"Pi_full": {c: [cf.get(c, {}).get("gmax"), (sf.get(c, {}).get("gmax_rule") or {}).get("gmax_kind")] for c in CLS},
                             "Pi_train": {c: [ct.get(c, {}).get("gmax"), (st.get(c, {}).get("gmax_rule") or {}).get("gmax_kind")] for c in CLS}}), MEAS, P + " §5.2")
    add("gsigma_max_by_class", jv({"Pi_full": {c: cf.get(c, {}).get("gsig_max") for c in CLS}, "Pi_train": {c: ct.get(c, {}).get("gsig_max") for c in CLS}}), MEAS, P + " §5.2")
    add("a90_by_class_bin", jv({"Pi_full": {c: [cf.get(c, {}).get("a90_edges"), cf.get(c, {}).get("a90_vals")] for c in CLS}, "Pi_train": {c: [ct.get(c, {}).get("a90_edges"), ct.get(c, {}).get("a90_vals")] for c in CLS}}), MEAS, P + " §5.2")
    add("tstruct_by_class", jv({"Pi_full": {c: [(sf.get(c, {}).get("gmax_rule") or {}).get("t_struct"), (sf.get(c, {}).get("gmax_rule") or {}).get("t_struct_truncated")] for c in CLS},
                                "Pi_train": {c: [(st.get(c, {}).get("gmax_rule") or {}).get("t_struct"), (st.get(c, {}).get("gmax_rule") or {}).get("t_struct_truncated")] for c in CLS}}), MEAS, P + " §5.3")
    # пары: пол sigma, Q5
    selp = {c: jl(os.path.join(O, "k7", "selection_%s.json" % c)) or {} for c in CLS}
    q5f = jl(os.path.join(O, "calib", "q5_detail_full.json")) or {}
    q5t = jl(os.path.join(O, "train", "q5_detail_train.json")) or {}
    lf = os.path.join(ld, "pairs_q5_floor.csv")
    mdir = os.path.join(O, "calib", "cache", "L%d" % a.Lf, "meta")
    with open(lf, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["class", "pid", "cell", "floor_bps", "n_valid", "full_n_reg", "full_phi15", "full_hl15", "full_hl15_hi", "full_q5_2h", "full_q5_6h", "full_q5_24h",
                    "train_n_reg", "train_phi15", "train_hl15_hi", "train_q5_2h", "train_q5_6h", "train_q5_24h"])
        for c in CLS:
            gp = os.path.join(O, "calib", "grid_full", c)
            pids = jl(os.path.join(gp, "pids.json")) or []
            cells = (jl(os.path.join(gp, "pairmeta.json")) or {}).get("cells", [])
            for pid, cell in zip(pids, cells):
                m = jl(os.path.join(mdir, pid + ".json")) or {}
                qf, qt = q5f.get(pid) or {}, q5t.get(pid) or {}

                def ok(q, T):
                    return int(q.get("hl_hi") is not None and q.get("phi15") is not None and 0 < q["phi15"] < 1 and q["hl_hi"] <= T / 4.0)
                w.writerow([c, pid, cell, m.get("floor"), m.get("n_valid"), qf.get("n_reg"), qf.get("phi15"), qf.get("hl15"), qf.get("hl_hi"), ok(qf, 120), ok(qf, 360), ok(qf, 1440),
                            qt.get("n_reg"), qt.get("phi15"), qt.get("hl_hi"), ok(qt, 120), ok(qt, 360), ok(qt, 1440)])
    pth, h = "%s/pairs_q5_floor.csv" % a.prefix, sha(lf)
    add("sigma_floor_by_pair", pth, MEAS, "sha256=%s; колонка floor_bps; правило %s §4.1, §7.1 K1" % (h, P))
    add("pair_stationarity_list", pth, MEAS, "sha256=%s; колонки full_q5_*, train_q5_* (допуск пары по HL15_верх <= T/4); правило %s §7.2" % (h, P))
    # выбор класса, клетки
    cp = os.path.join(ld, "class_params.csv")
    with open(cp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["class", "formed", "theta", "k", "p", "T_min", "s_stop", "c_train", "plateau", "agree", "a3b_H0toH1", "a3b_H1toH0", "reasons"])
        for c in CLS:
            s = selp[c]
            cf_ = s.get("c_full") or [None] * 5
            a3 = s.get("a3b") or [None, None]
            w.writerow([c, s.get("formed"), cf_[0], cf_[1], cf_[2], cf_[3], cf_[4], jv(s.get("c_train")), s.get("plateau"), s.get("agree"), a3[0], a3[1], "; ".join(s.get("reasons") or [])])
    pth, h = "%s/class_params.csv" % a.prefix, sha(cp)
    add("class_params_selected", pth, MEAS, "sha256=%s; выбор на уровне класса (LCB), %s §7.4" % (h, P))
    cells_rows = []
    for c in CLS:
        for r in rcsv(os.path.join(O, "k7", "cells_%s.csv" % c)):
            r["class"] = c
            s = selp[c]
            cf_ = s.get("c_full") or [None] * 5
            r.update(theta=cf_[0], k=cf_[1], p=cf_[2], T_min=cf_[3], s_stop=cf_[4])
            cells_rows.append(r)
    cc = os.path.join(ld, "cells.csv")
    cols = ["class", "cell", "pairs", "theta", "k", "p", "T_min", "s_stop", "trades", "assets", "days", "mean", "lo_t", "lo_boot", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "admitted", "tradable", "share_net_stale", "a3_mean"]
    with open(cc, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in cells_rows:
            w.writerow(r)
    pth, h = "%s/cells.csv" % a.prefix, sha(cc)
    admitted = [r["cell"] for r in cells_rows if r.get("tradable") == "1"]
    add("cells_admitted", jv(admitted), MEAS, "число допущенных клеток M = %d из %d; таблица %s sha256=%s; %s §7.5" % (len(admitted), len(cells_rows), pth, h, P))
    # списки заданий окна: W1 - пары допущенных клеток (с допуском Q5 при T класса), W2 - связки среза «как в видео» (всегда)
    jw1 = os.path.join(ld, "jobs_w1.csv")
    with open(jw1, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator=chr(10))
        w.writerow(["class", "cell", "pid", "theta", "k", "p", "T_min", "s_stop", "q5_admitted_at_T"])
        qr = {r["pid"]: r for r in rcsv(lf)}
        n_w1 = 0
        for c in CLS:
            s_ = selp[c]
            cf_ = s_.get("c_full") or [None] * 5
            adm_c = {r["cell"] for r in rcsv(os.path.join(O, "k7", "cells_%s.csv" % c)) if r["tradable"] == "1"}
            if not adm_c:
                continue
            for r in qr.values():
                if r["class"] == c and r["cell"] in adm_c:
                    tcol = {120: "full_q5_2h", 360: "full_q5_6h", 1440: "full_q5_24h"}.get(int(cf_[3]))
                    w.writerow([c, r["cell"], r["pid"], cf_[0], cf_[1], cf_[2], cf_[3], cf_[4], r.get(tcol)])
                    n_w1 += 1
    pth1, h1 = "%s/jobs_w1.csv" % a.prefix, sha(jw1)
    add("jobs_w1", pth1, MEAS, "sha256=%s; строк %d (пар допущенных клеток); пусто, если допущенных клеток нет" % (h1, n_w1))
    hs = jl(os.path.join(O, "k7", "hints_c2_summary.json")) or []
    jw2 = os.path.join(ld, "jobs_w2.csv")
    with open(jw2, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator=chr(10))
        w.writerow(["name", "ex1", "sym1", "ex2", "sym2", "in_universe", "market", "pid"])
        for h_ in hs:
            w.writerow([h_["name"], h_["ex1"], h_["sym1"], h_["ex2"], h_["sym2"], h_["in_universe"], h_.get("market"), h_.get("pid")])
    add("jobs_w2", "%s/jobs_w2.csv" % a.prefix, MEAS, "sha256=%s; связки среза «как в видео» §10.7 (читается всегда, статуса нет)" % sha(jw2))
    n5 = os.path.join(os.path.dirname(O.rstrip("/")), "out_noq5", "k7")
    if os.path.isdir(n5):
        res5 = {}
        for c in CLS:
            s5 = jl(os.path.join(n5, "selection_%s.json" % c)) or {}
            cr5 = rcsv(os.path.join(n5, "cells_%s.csv" % c))
            res5[c] = dict(formed=s5.get("formed"), c_full=s5.get("c_full"), c_train=s5.get("c_train"), reasons=s5.get("reasons"), a3b=s5.get("a3b"),
                           cells_admitted=s5.get("cells_admitted"), cells_pass_A=[sum(1 for r in cr5 if r["A%d" % i] == "1") for i in range(1, 8)])
        add("q5_off_sensitivity", jv(res5), MEAS, P + " §7.2: доля Q5 монотонна по плотности - вариант без Q5 посчитан отдельно (out_noq5), основной результат - с Q5; какой вариант основной - решает CEO")
    add("p_win", jv({c: selp[c].get("p_win") for c in CLS}), MEAS, P + " §7.6 K8 (в); перестановка знака 1000, seed 37")
    for c in CLS:
        for fn in ("grid_class_%s.csv" % c, "grid_cells_%s.csv" % c):
            if os.path.isfile(os.path.join(O, "k7", fn)):
                pth, h = put(os.path.join(O, "k7", fn), fn)
                add("full_grid_csv_" + fn.replace(".csv", ""), pth, MEAS, "sha256=" + h)
    add("full_grid_csv", "grid_class_<класс>.csv (180 комбинаций × класс) и grid_cells_<класс>.csv (180 × ячейка)", MEAS, P + " §7.4")
    # sigma_form: для допущенных клеток; иначе пусто
    add("sigma_form_by_cell", jv({}) if not admitted else "см. cells_admitted", MEAS, P + " §7.1 K9: клеток нет - σ_форм не определяется" if not admitted else P + " §7.1 K9")
    add("pair_lists_by_cell", "pairs_q5_floor.csv (колонка cell)", MEAS, "sha256=%s" % sha(lf))
    kb = {c: (selp[c].get("katok_bounds") or {}) for c in CLS}
    add("katok_quartiles", jv(kb), MEAS, P + " §10.9 (квартили 0,25/0,75 по сетке класса, K9)")
    br = {c: (selp[c].get("counters_selected") or {}) for c in CLS}
    add("breaks_table_formation", jv(br), MEAS, P + " §10.6: счётчики выбранной комбинации класса (запреты по причинам, стопы, исключения)")
    g8 = os.path.join(O, "size", "g_c2_8.json")
    if os.path.isfile(g8):
        pth, h = put(g8, "g_c2_8.json")
        add("g_c2_8", pth, MEAS, "sha256=" + h + "; тест размера A2 для n = 4, 6, 7, 11, 13 и сценария победителя из K")
    al = (jl(os.path.join(O, "k7", "summary.json")) or {}).get("_all", {})
    add("calib_multiplicity_result", jv(al), MEAS, P + " §7.7")
    # реализации
    for k, v in (("impl_R_K2", "R-K2: d_L = s - b_L с причинным b_L; разброс пары - MAD на одном наборе минут 2468…last_day, где определены оба d; медиана по парам класса; сравниваются крипта и акции+ETF"),
                 ("impl_R_HK", "R-HK: метка рынка «Гонконг» присваивается АКТИВУ (контракт HKD у Bitget или код HK#### у Binance в любой строке asset_pairs.csv), а не паре; в r2-коде метка шла по паре (26 пар, ушедших в «прочие TradFi», вернулись в основной ряд)"),
                 ("impl_R_MEM", "R-MEM: команда grid пишет суточные таблицы потоково в .npy; байт в байт совпадает с прежней записью на 63 парах × 180 комбинаций (tab, act, hist, minnet, pids, pairmeta, counters, trades)"),
                 ("impl_R_A3b", "R-A3b: половины активов считаются отдельными прогонами со структурными числами только своей половины; комбинация половины проверяется на парах другой половины с замороженными числами первой (в r2-коде внутри полной сетки это был бы приближённый путь)"),
                 ("impl_R_metal", "металлы+энергия (146 пар) вне основного ряда: календарь CME не задан (§3.6) и в addendum не вносился"),
                 ("impl_R_G8", "G-C2-8: A2 = обе нижние границы > 0 (блочный t, бутстреп-t B = 3000 seed 37); победитель из K независимых нулевых рядов, K = 12 и 180 (истинная зависимость комбинаций неизвестна)")):
        add(k, v, ASSIGN, P + " (реализация)")
    # sha256 кода
    for p in sorted(glob.glob(os.path.join(a.code_dir, "*.py"))):
        add("code_sha256_" + os.path.basename(p), sha(p), MEAS, "файл на Deck в момент калибровки")
    add("code_sha256_params_g0_1", sha(os.path.join(a.code_dir, "P-38-params-2026-11-07.csv")), MEAS, "копия params G0 часть 1 на Deck (CRLF/LF могут различаться с закоммиченной)")
    out_csv = os.path.join(a.dest, a.csv_name)
    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_NONNUMERIC)
        w.writerow(["key", "value", "status", "when", "source"])
        for r in rows:
            w.writerow(r)
    print("addendum: строк %d, клеток допущено %d, файл %s sha256=%s" % (len(rows), len(admitted), out_csv, sha(out_csv)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
