#!/usr/bin/env python3
"""TK-493, задача C: сверка «числа отчёта = числа csv» (независимо от build_report.py: свой разбор спецификаций и свой формат).

1. Каждое число из report_numbers.json (спецификация -> значение из json/csv расчёта) пересчитывается заново и сравнивается с текстом на указанной строке отчёта.
2. Перекрёстные проверки: числа выжимок (summary_*.json, считаны из сеток) против строк csv (grid_class_*.csv, cells*.csv) для четырёх наборов.
3. sha256 файлов, перечисленных в addendum с Gate, против файлов каталога списков (по LF).
Запуск из корня репозитория:  python docs/findings/xdl9-conv-formation-gate-2026-11-07/check_numbers.py   (код возврата 0 — всё сошлось; пишет check_result.txt)
"""
import csv
import hashlib
import json
import os
import re
import sys
import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
T = "docs/findings/xdl9-conv-formation-gate-2026-11-07"
L1 = "docs/research/P-38-calib-gate-2026-11-07"
REPORT = "docs/findings/xdl9-conv-formation-gate-2026-11-07.md"
ADD = "docs/research/P-38-params-calib-gate-2026-11-07.csv"
NAMES = {"g1": ("gate_q5", "gate", L1 + "/grid_class_crypto.csv", L1 + "/cells.csv"),
         "g2": ("gate_noq5", "gate_noq5", T + "/bundle/grid_class_crypto_noq5.csv", T + "/bundle/cells_crypto_noq5.csv"),
         "n1": ("nogate_q5", "nogate", T + "/bundle_nogate/grid_class_crypto_nogate.csv", T + "/bundle_nogate/cells_crypto_nogate.csv"),
         "n2": ("nogate_noq5", "nogate_noq5", T + "/bundle_nogate/grid_class_crypto_noq5_nogate.csv", T + "/bundle_nogate/cells_crypto_noq5_nogate.csv")}


def J(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def CSV(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def num(x):
    return None if x in ("", "None", None) else float(x)


def walk(o, path):
    for k in path.split("."):
        o = o[int(k)] if isinstance(o, list) else o[k]
    return o


def fmt(v, code):
    if v is None:
        return "—"
    if code == "i":
        return "%d" % round(v)
    if code == "yn":
        return "да" if v else "нет"
    if code == "s":
        return str(v)
    m = re.fullmatch(r"(s?)(pc)?f(\d)", code)
    if m:
        sgn, pc, nd = m.group(1) == "s", m.group(2) == "pc", int(m.group(3))
        x = v * 100 if pc else v
        body = ("%." + str(nd) + "f") % abs(x)
        if float(body) == 0:
            res = body
        elif x < 0:
            res = "\u2212" + body
        else:
            res = ("+" if sgn else "") + body
        return res.replace(".", ",") + (" %" if pc else "")
    if code == "e2":
        m_, e_ = ("%.2e" % v).split("e")
        digs = {"0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074", "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079", "-": "\u207b"}
        return m_.replace(".", ",") + "\u00b710" + "".join(digs[ch] for ch in str(int(e_)))
    raise ValueError(code)


def combo_text(c):
    th, k, p, t, s = c
    ths = ("%g" % th).replace(".", ",")
    ts = ("%g" % (t / 60)).replace(".", ",")
    return "θ %s; k %d; p %d; T %s ч; %s" % (ths, k, p, ts, "S0 = 2" if s else "только S1")


def sel_file(k):
    return T + "/data/k7_%s_selection_crypto.json" % NAMES[k][1]


def cfull_row(k):
    c = J(sel_file(k))["c_full"]
    hit = []
    for r in CSV(NAMES[k][2]):
        if abs(float(r["theta"]) - c[0]) < 1e-9 and int(r["k"]) == c[1] and int(r["p"]) == c[2] and int(r["T"]) == c[3]:
            if (c[4] is None and r["s_stop"] in ("", "None")) or (c[4] is not None and r["s_stop"] not in ("", "None") and abs(float(r["s_stop"]) - c[4]) < 1e-9):
                hit.append(r)
    assert len(hit) == 1, (k, c, len(hit))
    return hit[0]


def value(spec):
    kind = spec[0]
    if kind == "j":
        rel = spec[1]
        if rel in NAMES:
            rel = T + "/data/summary_%s.json" % NAMES[rel][0]
        return walk(J(rel), spec[2])
    if kind == "cf":
        return num(cfull_row(spec[1])[spec[2]])
    if kind == "cnt":
        rows = CSV(NAMES[spec[1]][2])
        pr = spec[2]
        def ok(r):
            m, lo = num(r["mean"]), num(r["lo"])
            return {"pos": m is not None and m > 0, "pos_p3": m is not None and m > 0 and int(r["p"]) == 3, "ok": r["ok"] == "True",
                    "pos_lo": lo is not None and lo > 0, "pos_lo_ok": r["ok"] == "True" and lo is not None and lo > 0, "p3": int(r["p"]) == 3, "all": True}[pr]
        return sum(1 for r in rows if ok(r))
    if kind == "cells":
        k, i = spec[1], spec[2]
        rows = [r for r in CSV(NAMES[k][3]) if "class" not in r or r["class"] == "crypto"]
        if i == 0:
            return len(rows)
        if i == 99:
            return sum(1 for r in rows if r["tradable"] == "1")
        return sum(1 for r in rows if r["A%d" % i] == "1")
    if kind == "hx":
        h = J(T + "/hints_gate_extra/hints_c2_summary.json")
        g = [r for r in h if "gate" in r["name"].lower()]
        cr = [r for r in g if r["name"].split()[0] in ("H2", "H3", "H4", "H4c", "H6")]
        mn = [r for r in cr if r.get("in_main_series")]
        nm = spec[1]
        return {"gate_all": len(g), "gate_in_universe": sum(1 for r in g if r["in_universe"]), "gate_crypto": len(cr), "gate_crypto_main": len(mn),
                "gate_hk_tradfi": sum(1 for r in g if r["name"].split()[0] == "H1"), "gate_crypto_main_grid_pos": sum(1 for r in mn if (r.get("grid_trades_total_all_combos") or 0) > 0),
                "gate_crypto_main_sel_trades": sum((r.get("at_selected") or {}).get("trades", 0) for r in mn),
                "gate_crypto_main_grid_trades": sum(r.get("grid_trades_total_all_combos") or 0 for r in mn)}[nm]
    if kind == "hxn":
        h = J(T + "/hints_gate_extra/hints_c2_summary.json")
        return [r for r in h if r["name"] == spec[1]][0][spec[2]]
    if kind == "w2":
        rows = [r for r in CSV(T + "/hints_gate_extra/hints_w2_formation.csv") if "gate" in r["name"].lower()]
        cr = [r for r in rows if r["name"].split()[0] in ("H2", "H3", "H4", "H4c", "H6")]
        hk = [r for r in rows if r["name"].split()[0] == "H1"]
        has = lambda r: num(r["net_taker_p1_mean"]) is not None
        r20 = [r for r in cr if r["var"] == "rel" and float(r["theta"]) == 20 and has(r)]
        l80c = [r for r in cr if float(r["theta"]) <= 80 and has(r)]
        l80h = [r for r in hk if float(r["theta"]) <= 80 and has(r)]
        nm = spec[1]
        if nm == "crypto_rel20_pairs":
            return len(r20)
        if nm == "crypto_rel20_trades":
            return sum(int(r["trades_p1"]) for r in r20)
        if nm == "crypto_rel20_net":
            return sum(int(r["trades_p1"]) * num(r["net_taker_p1_mean"]) for r in r20) / sum(int(r["trades_p1"]) for r in r20)
        if nm == "crypto_rel20_net_max":
            return max(num(r["net_taker_p1_mean"]) for r in r20)
        if nm == "crypto_rel20_net_min":
            return min(num(r["net_taker_p1_mean"]) for r in r20)
        if nm == "crypto_le80_rows":
            return len(l80c)
        if nm == "crypto_le80_pos":
            return sum(1 for r in l80c if num(r["net_taker_p1_mean"]) > 0)
        if nm == "crypto_le80_pos_trades_max":
            return max([int(r["trades_p1"]) for r in l80c if num(r["net_taker_p1_mean"]) > 0] or [0])
        if nm == "hk_le80_rows":
            return len(l80h)
        if nm == "hk_le80_pos":
            return sum(1 for r in l80h if num(r["net_taker_p1_mean"]) > 0)
        if nm == "hk_le80_pos_ge20":
            return sum(1 for r in l80h if num(r["net_taker_p1_mean"]) > 0 and int(r["trades_p1"]) >= 20)
    if kind == "w2row":
        for r in CSV(T + "/hints_gate_extra/hints_w2_formation.csv"):
            if r["name"] == spec[1] and r["var"] == spec[2] and float(r["theta"]) == spec[3]:
                return num(r[spec[4]])
    if kind == "g8":
        v = [r["p_winner_a2"] for r in J(L1 + "/g_c2_8.json")["winner"] if r["K"] == spec[1]]
        return 100 * (min(v) if spec[2] == "min" else max(v))
    if kind == "a8":
        d = J(sel_file(spec[1]))["a8"]
        return len(d) if spec[2] == "len" else sum(d.values())
    if kind == "r31":
        s = J(sel_file(spec[1]))
        return bool(num(cfull_row(spec[1])["lo"]) > 0 and s["plateau"] and s["agree"])
    if kind == "eqc":
        return J(sel_file(spec[1]))["c_full"] == J(sel_file(spec[2]))["c_full"]
    if kind == "turn":
        return int(re.search(r"панелей оборота (\d+)", open(os.path.join(ROOT, T, "logs/turn.log"), encoding="utf-8").read()).group(1))
    if kind == "dur":
        ls = open(os.path.join(ROOT, T, "logs/pipe.status"), encoding="utf-8").read().split("\n")
        t = lambda ln: datetime.datetime.strptime(re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", ln).group(0), "%Y-%m-%dT%H:%M:%SZ")
        a = next(ln for ln in ls if spec[1] in ln)
        b = next(ln for ln in ls if spec[2] in ln)
        return (t(b) - t(a)).total_seconds() / 60
    if kind == "logre":
        txt_ = open(os.path.join(ROOT, T, spec[1]), encoding="utf-8").read()
        hits = re.findall(spec[2], txt_)
        h = hits[spec[4]]
        return float(h[spec[3] - 1] if isinstance(h, tuple) else h)
    if kind == "const":
        return spec[1]
    if kind == "x":
        a, b = value(spec[2]), value(spec[3])
        op = spec[1]
        if op == "sub":
            return a - b
        if op == "add":
            return a + b
        if op == "pct":
            return 100 * a / b
        if op == "mul21div13":
            return a * 21 / 13
        if op == "div":
            return a / b
    raise KeyError(spec)


def main():
    ledger = J(T + "/report_numbers.json")
    lines = open(os.path.join(ROOT, REPORT), encoding="utf-8").read().split("\n")
    bad, ok = [], 0
    for e in ledger:
        v = value(e["spec"])
        txt = combo_text(v) if e["code"] == "combo" else fmt(v, e["code"])
        if txt != e["text"]:
            bad.append(("значение", e["id"], e["spec"], txt, e["text"]))
            continue
        ln = lines[e["line"] - 1] if 0 < e["line"] <= len(lines) else ""
        if txt not in ln:
            bad.append(("нет в строке отчёта", e["id"], e["line"], txt))
            continue
        ok += 1
    # 2. перекрёстные проверки: summary (из сеток) против csv
    cross, cbad = 0, []
    for k, (sname, selname, gpath, cpath) in NAMES.items():
        s = J(T + "/data/summary_%s.json" % sname)["classes"]["crypto"]
        r = cfull_row(k)
        pairs = [("trades", s["at_cfull"]["trades"], num(r["trades"])), ("mean", s["at_cfull"]["mean"], num(r["mean"])), ("lo", s["at_cfull"]["lo"], num(r["lo"])), ("hi", s["at_cfull"]["hi"], num(r["hi"])),
                 ("mean_trade", s["at_cfull"]["mean_trade"], num(r["mean_trade"])), ("assets", s["at_cfull"]["assets"], num(r["assets"]))]
        rows = CSV(gpath)
        pairs += [("grid_pos_mean", s["grid_pos_mean"], sum(1 for x in rows if num(x["mean"]) is not None and num(x["mean"]) > 0)),
                  ("grid_ok_rows", s["grid_ok_rows"], sum(1 for x in rows if x["ok"] == "True")),
                  ("grid_pos_lo_ok", s["grid_pos_lo_ok"], sum(1 for x in rows if x["ok"] == "True" and num(x["lo"]) is not None and num(x["lo"]) > 0)),
                  ("cells_n", s["cells_n"], value(["cells", k, 0])), ("cells_admitted", s["cells_admitted_n"], value(["cells", k, 99]))]
        for i in range(1, 8):
            pairs.append(("cells_pass_A%d" % i, s["cells_pass_A"][i - 1], value(["cells", k, i])))
        for nm, a, b in pairs:
            cross += 1
            if a is None or b is None or abs(a - b) > 1e-6 * max(1.0, abs(b)):
                cbad.append((k, nm, a, b))
    # 3. sha256 списков из addendum
    sbad, sn = [], 0
    for r in CSV(ADD):
        m = re.search(r"sha256=([0-9a-f]{64})", r["source"] + " " + r["value"])
        pth = None
        if r["key"].startswith("file_sha256_"):
            pth = r["value"].split(" ")[0]
            m = re.search(r"sha256=([0-9a-f]{64})", r["source"])
        elif r["source"].startswith("sha256="):
            pth = r["value"] if r["value"].startswith("docs/") else None
        if pth and m and os.path.isfile(os.path.join(ROOT, pth)):
            sn += 1
            h = hashlib.sha256(open(os.path.join(ROOT, pth), "rb").read().replace(b"\r\n", b"\n")).hexdigest()
            if h != m.group(1):
                sbad.append((r["key"], pth))
    res = ("Сверка `check_numbers.py` (независимый разбор спецификаций, свой формат): чисел из расчёта в отчёте %d, совпало с источником и стоит на указанной строке %d, расхождений %d; "
           "перекрёстных сравнений выжимок (считаны из сеток) со строками csv %d, расхождений %d; sha256 списков из addendum сверено %d, расхождений %d. "
           "Вручную (не из расчёта) записаны: константы постановки и протокола (13 и 6 суток, 180 комбинаций, 90 %%-интервал, 12,6 bps = 4 × 3,15, номера суток и пунктов, 17 и 16 строк jobs_w2, 1 440 минут базиса и т. п.), "
           "числа манифеста Gate (1072 символа, 15 контрактов enable_decimal, 15 пред-рыночных; `docs/research/P-38-calib-gate-2026-11-07/gate_ready_manifest_gate.txt`) и времена K0." % (
               len(ledger), ok, len(bad), cross, len(cbad), sn, len(sbad)))
    print(res)
    for b in (bad + cbad + sbad)[:20]:
        print("РАСХОЖДЕНИЕ:", b)
    open(os.path.join(ROOT, T, "check_result.txt"), "w", encoding="utf-8", newline="\n").write(res + "\n")
    return 0 if not (bad or cbad or sbad) else 1


if __name__ == "__main__":
    sys.exit(main())
