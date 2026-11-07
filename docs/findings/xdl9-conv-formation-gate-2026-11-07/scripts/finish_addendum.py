#!/usr/bin/env python3
"""TK-493, задача C: достройка addendum с Gate. Берёт CSV, собранный на Deck через conv_addendum (набор Gate), заменяет строки, описывающие состояние Gate, и добавляет строки:
кандидаты окна (R3.1/R3.7 п. 4), условия R3.1 по четырём наборам, sha256 списков входов раннера по LF, интерпретации (назначено CEO без Судьи, подтвердить), доказательства «окно не читалось».
Запуск из корня репозитория:  python docs/findings/xdl9-conv-formation-gate-2026-11-07/scripts/finish_addendum.py SRC.csv
Результат: docs/research/P-38-params-calib-gate-2026-11-07.csv
"""
import csv
import hashlib
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
T = "docs/findings/xdl9-conv-formation-gate-2026-11-07"
L1 = "docs/research/P-38-calib-gate-2026-11-07"
OUT = "docs/research/P-38-params-calib-gate-2026-11-07.csv"
WHEN = "07.11 G0 ч.2 (Gate)"
MEAS = "измерено по правилу протокола на формировании (сутки 2465…2478)"
ASSIGN = "назначено CEO без Судьи, подтвердить"
P = "docs/research/P-38-convergence-xdl9.md"
SETS = {"nogate_V1": ("nogate", "nogate_q5"), "nogate_V2": ("nogate_noq5", "nogate_noq5"), "gate_V1": ("gate", "gate_q5"), "gate_V2": ("gate_noq5", "gate_noq5")}


def J(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def sha_lf(rel):
    return hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read().replace(b"\r\n", b"\n")).hexdigest()


def jv(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda o: None if (isinstance(o, float) and not math.isfinite(o)) else str(o))


def main():
    src = sys.argv[1]
    rows = list(csv.DictReader(open(src, encoding="utf-8", newline="")))
    keep = []
    drop = {"params_calib_state", "calib_start_json"}
    for r in rows:
        if r["key"] in drop:
            continue
        keep.append([r["key"], r["value"], r["status"], WHEN, r["source"]])
    out = []

    def add(key, value, status, source):
        out.append([key, value, status, WHEN, source])
    c0 = J(T + "/data/calib_start.json")
    add("params_calib_state", "G0 часть 2 (вторая): калибровка на формировании С Gate по П-38 r2 + r3 (R3.7); окно (сутки >= 2479) не читалось; значения получены кодом conv_*.py (sha256 - строки code_sha256_*, коммит кода "
        "5162113fa0335fc006b664d8c049825610b5854c) по правилам протокола; addendum без Gate (P-38-params-calib-2026-11-07.csv, f47dff41) не менялся и воспроизведён тем же кодом с --no-gate побайтно; "
        "строки code_sha256_* здесь - файлы на Deck (LF) в момент калибровки", ASSIGN, P + " §12.1, §17 R3.7")
    add("calib_start_json", "%s; gate_ready существует: %s; валиден как манифест §3.4: %s (проверки %s, symbols=%s); Gate в универсум C2 ВКЛЮЧЁН (manifest valid, таблица пар Gate по R2' asset_pairs_gate.csv); "
        "контроль без Gate (--no-gate) посчитан отдельными выходами; K0 записан в ~/xdl9/c2g/calib_start.json ДО чтения панелей" % (
            c0["time_utc"], c0["gate_ready_exists"], c0["gate_ready_valid"], ",".join(c0["gate_checks"]), c0["gate_nsym"]), MEAS, "%s/data/calib_start.json sha256=%s" % (T, sha_lf(T + "/data/calib_start.json")))
    add("calib_gate_code_commit", "5162113fa0335fc006b664d8c049825610b5854c", MEAS, "git archive HEAD (tools/compute/statarb, tools/xdl, params, протокол) -> ~/xdl9/c2g/repo, LF; sha256 params и r3 равны blob; "
        "тесты на Deck: test_conv.py 27/27, test_conv_gate.py 9/9, test_conv_window.py 13/13 (%s/logs/tests1.log, tests2.log)" % T)
    add("gate_manifest", jv({"sha256": c0["gate_sha256"], "symbols": c0["gate_nsym"], "checks": c0["gate_checks"], "valid": c0["gate_ready_valid"]}), MEAS, "~/xdl9/gate_ready; docs/research/P-38-calib-2026-11-07/gate_ready_manifest_gate.txt")
    cnt = J(T + "/data/nogate_vs_old_counts.json")
    add("nogate_control", jv({"compared": cnt["total"], "equal": cnt["eq"], "diff": cnt["diff"]}), MEAS, "%s/data/nogate_vs_old.txt: ~/xdl9/c2g/out_nogate и out_nogate_noq5 (новый код, --no-gate) против ~/xdl9/c2/out и out_noq5 (f47dff41): tab/act/hist/minnet/pids/pairmeta/counters/trades, selection, cells, grid_*.csv, classpar, q5" % T)
    # R3.1 по четырём наборам
    r31 = {}
    for key, (selname, sname) in SETS.items():
        sel = J(T + "/data/k7_%s_selection_crypto.json" % selname)
        sm = J(T + "/data/summary_%s.json" % sname)["classes"]["crypto"]
        ex = J(T + "/data/extra_%s.json" % sname)["crypto"]
        a = sm["at_cfull"]
        d = dict(c_full=sel["c_full"], c_train=sel["c_train"], trades=a["trades"], assets=a["assets"], y_d=a["mean"], lo=a["lo"], hi=a["hi"], net_trade=a["mean_trade"], a_lo_gt0=bool(a["lo"] > 0),
                 b_plateau=sel["plateau"], c_agree=sel["agree"], step_distance=ex["step_distance"], r31_pass=bool(a["lo"] > 0 and sel["plateau"] and sel["agree"]), a3b=sel["a3b"], a8_all=bool(all(sel["a8"].values())),
                 cells_pass_A=sm["cells_pass_A"], cells_n=sm["cells_n"], cells_admitted=sm["cells_admitted_n"])
        r31[key] = d
        add("r31_" + key, jv(d), MEAS, P + " §17 R3.1 (условия (а)-(в), метки A3b, A3, A8); %s/data/k7_%s_selection_crypto.json, %s/data/summary_%s.json" % (T, selname, T, sname))
    ok_gate = r31["gate_V1"]["r31_pass"]
    add("window_set_decision", "gate" if ok_gate else "nogate", MEAS, P + " §17 R3.7 п. 4: набор 'с Gate', если Gate включён по п. 1 и класс крипты с Gate удовлетворяет R3.1; иначе 'без Gate'. Gate включён (манифест валиден); "
        "R3.1 с Gate: V1 %s, V2 %s (условие (в) не выполнено у обоих: c_обуч и c_полн отличаются на два шага по T) -> набор без Gate; набор с Gate - отдельные описательные строки на окне, вне M_окно" % (
            r31["gate_V1"]["r31_pass"], r31["gate_V2"]["r31_pass"]))
    sp = {k: J(L1 + "/window_spec_%s.json" % k) for k in ("nogate", "gate")}
    cands = []
    for key, tag in (("nogate_V1", "nogate"), ("nogate_V2", "nogate")):
        v = [x for x in sp[tag]["variants"] if x["id"] == key.split("_")[1]][0]
        d = r31[key]
        cands.append(dict(set="nogate", variant=key.split("_")[1], cls="crypto", combo=d["c_full"], in_M_window=True, lcb=d["lo"], y_d=d["y_d"], trades=d["trades"], assets=d["assets"],
                          sigma_form=v["sigma_form"], labels=[v["formation_labels"][k]["text"] for k in ("a3b", "a3_cells", "a8")], expected_trades_window=round(d["trades"] / 13.0 * 21.0, 1)))
    add("window_candidates", jv(cands), MEAS, P + " §17 R3.1, R3.2: класс 'крипта', варианты V1 (Q5 включён) и V2 (Q5 выключен), набор без Gate; M_окно = 2; акции+ETF (граница y_d < 0), металлы+энергия, прочие TradFi, индексы, фиат, Gate-TradFi - вне окна")
    desc = []
    for key in ("gate_V1", "gate_V2"):
        d = r31[key]
        desc.append(dict(set="gate", variant=key.split("_")[1], cls="crypto", combo=d["c_full"], in_M_window=False, lcb=d["lo"], y_d=d["y_d"], trades=d["trades"], r31_pass=d["r31_pass"],
                         expected_trades_window=round(d["trades"] / 13.0 * 21.0, 1)))
    add("window_candidates_gate_descriptive", jv(desc), MEAS, P + " §17 R3.7 п. 4: строки с Gate - отдельные, вне M_окно, описательно; раннер окна conv_window.py Gate-варианты не принимает (нужна доработка раннера и новый токен)")
    add("M_window", "2", MEAS, P + " §17 R3.2: число объявленных вариантов класса, идущих в окно (V1, V2; набор без Gate)")
    add("expected_trades_window", jv({"nogate_V1": cands[0]["expected_trades_window"], "nogate_V2": cands[1]["expected_trades_window"], "rule": "сделок на c_полн / 13 суток * 21 сутки"}), MEAS, P + " §17 R3.3 (ожидание до окна)")
    add("sigma_form_by_variant", jv({"nogate_V1": cands[0]["sigma_form"], "nogate_V2": cands[1]["sigma_form"], "gate_V1": [x for x in sp["gate"]["variants"] if x["id"] == "V1"][0]["sigma_form"],
                                      "gate_V2": [x for x in sp["gate"]["variants"] if x["id"] == "V2"][0]["sigma_form"]}), MEAS, P + " §17 R3.2 (M_эфф), params sigma_form_by_cell; std суточного y_d по суткам 2472..2478, conv_window.py make-spec; spec: %s/window_spec_*.json" % L1)
    # sha256 списков входов раннера по LF
    files = [("nogate_V1", ["classpar_full_nogate.json", "classpar_H0_nogate.json", "classpar_H1_nogate.json", "classpar_train_nogate.json", "q5_full_nogate.json"]),
             ("nogate_V2", ["classpar_full_nogate_noq5.json", "classpar_H0_nogate_noq5.json", "classpar_H1_nogate_noq5.json", "classpar_train_nogate_noq5.json"]),
             ("gate_V1", ["classpar_full.json", "classpar_H0.json", "classpar_H1.json", "classpar_train.json", "q5_full.json"]),
             ("gate_V2", ["classpar_full_noq5.json", "classpar_H0_noq5.json", "classpar_H1_noq5.json", "classpar_train_noq5.json"]),
             ("W2", ["jobs_w2.csv", "jobs_w2_nogate.csv"]), ("spec", ["window_spec_nogate.json", "window_spec_gate.json"])]
    for grp, names in files:
        for nm in names:
            rel = L1 + "/" + nm
            add("file_sha256_" + nm, "%s (набор/вариант: %s)" % (rel, grp), MEAS, "sha256=%s (по LF); %s" % (sha_lf(rel), P + " §17 R3.8 (входы раннера в токене)"))
    # доказательства, что окно не читалось
    wn = J(T + "/data/window_not_read.json")
    add("window_not_read_evidence", jv(wn), MEAS, "%s/data/window_not_read.json sha256=%s; ~/xdl9/c2g: window_reads.log и go_window.token отсутствуют; все meta.json сеток d1 <= 2478" % (T, sha_lf(T + "/data/window_not_read.json")))
    # прочтения и допущения
    for k, v in (("impl_gate_r31_two_variants", "R3.1 (а)-(в) проверены отдельно для V1 и V2 каждого набора; набор окна определяется по V1 и V2 вместе (с Gate оба не проходят (в))"),
                 ("impl_gate_nogate_shared_cache", "контроль без Gate: ряды (кэш cache/) общие с набором Gate - ячейки 8 бирж не зависят от Gate (байтовое равенство подтверждено прежним прогоном); K2, меры, сетки, выбор, проверки - отдельно (out_nogate, out_nogate_noq5)"),
                 ("impl_gate_k1b_g8_copied", "K1b и G-C2-8 перенесены из ~/xdl9/c2/out (sha256 conv_k1b.py и conv_size.py равны addendum 07.11; данные от Gate не зависят); K1b по парам Gate не считался"),
                 ("impl_gate_wrappers", "conv_report.py и conv_addendum.py зашивают путь K0 ~/xdl9/c2/calib_start.json: запуск через обёртки gx_tables.py и gx_addendum.py с подменой пути в процессе (код conv_* не менялся); срезы подсказок со всеми связками Gate - gx_hints_extra.py (расширяет HINTS в процессе)"),
                 ("impl_gate_jobs_w2", "jobs_w2.csv пересобран с H4c (TA Binance x Gate), 17 строк (jobs_w2_nogate.csv - тот же список по запуску без Gate); прежний P-38-calib-2026-11-07/jobs_w2.csv (16 строк) заменяется; W2 читается всегда"),
                 ("impl_gate_a3_label", "метка 'A3 по клеткам пройдено' ставится кодом (conv_window.py make-spec) только если A3 проходят ВСЕ клетки; без Gate - 0 из 28 клеток, с Gate - 5 из 36 (метка 'не пройдено')"),
                 ("impl_gate_runner", "раннер окна conv_window.py Gate-варианты не принимает (универсум раннера - 8 бирж): набор 'без Gate' исполним без правок; строки 'с Gate' на окне требуют доработки раннера и нового токена"),
                 ("impl_gate_threads", "Deck: до 4 потоков, nice -n 10, systemd-run --user MemoryMax; тесты - taskset -c 0-3")):
        add(k, v, ASSIGN, P + " (реализация); отчёт docs/findings/xdl9-conv-formation-gate-2026-11-07.md §3")
    # итоговый файл: сначала строки conv_addendum без заменённых, затем наши (порядок: состояние, остальное)
    final = [["key", "value", "status", "when", "source"]]
    head = [r for r in out if r[0] in ("params_calib_state", "calib_start_json")]
    tail = [r for r in out if r[0] not in ("params_calib_state", "calib_start_json")]
    final += head + keep + tail
    with open(os.path.join(ROOT, OUT), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_NONNUMERIC)
        for r in final:
            w.writerow(r)
    print("addendum:", OUT, "строк", len(final) - 1, "sha256(LF)", sha_lf(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
