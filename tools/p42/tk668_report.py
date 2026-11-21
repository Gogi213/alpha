#!/usr/bin/env python3
"""П-42 §13.68 (TK-668): таблицы отчёта из готовых результатов (`res/select68.json`, `e8-68.json`, `ref68.json`, `e4-lines.json`, `counts68-*.json`) → markdown в stdout.
Только форматирование: ничего не считает и не читает сделки. Запуск на srv: `PYTHONUTF8=1 python3 tk668_report.py > /tmp/tk668-tables.md`."""
from __future__ import annotations

import json
import os

ROOT = "/data/p42-tk668"
SRC = {"D": "вчерашний день", "W": "прошлая неделя", "P": "профиль объёма"}


def load(name):
    p = os.path.join(ROOT, "res", name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def f(x, nd=2, sign=True):
    return ("%+." + str(nd) + "f") % x if sign else ("%." + str(nd) + "f") % x


def months(m):
    return " / ".join("%d: %s" % (n, f(v, 0)) for n, v in m)


def rr_line(name, b):
    r = b["rr"]
    return "%s: R:R %.2f (средний выигрыш %s $, средний убыток %s $); стопов %d, убыток стопа средний %s $ (%.2f %% маржи 250 $ при плече 2), максимальный %s $ (%.2f %%)" % (
        name, r["rr"], f(r["mean_win"]), f(r["mean_loss"]), r["stops"]["n"], f(r["stops"]["mean_loss"]), r["stops"]["mean_loss_pct"], f(r["stops"]["max_loss"]), r["stops"]["max_loss_pct"])


def cells_table(sel, fam):
    out = ["| сторона | клетка | n | Σ $ | $/сделку | Sharpe | Δ ± SE $/сделку | z | p | ΔΣ ± SE $ | исключено | допуск | g1 | g2 | g3 | g4 |", "|---|---|---:|---:|---:|---:|---|---:|---:|---|---:|---|---|---|---|---|"]
    for s in ("L", "S"):
        base = sel["base"][s]
        out.append("| %s | **база** | %d | %s | %s | %.2f | | | | | | | | | | |" % (s, base["n"], f(base["sum"], 1), f(base["mean"], 3), base["sharpe"]))
        for cid, c in sel["cells"][s].items():
            if (cid.startswith("B")) != (fam == "B"):
                continue
            r, st = c["row"], c["stats"]
            out.append("| %s | %s | %d | %s | %s | %.2f | %s ± %.3f | %s | %.4f | %s ± %.1f | %d | %s | %s | %s | %s | %s |" % (
                s, cid, st["n"], f(st["sum"], 1), f(st["mean"], 3), st["sharpe"], f(r["d"], 3), r["se"], f(r["z"]), r["p"], f(r["dsum"], 1), r["se_sum"], r["n_ex"],
                "да" if r["admitted"] else "нет", *("да" if r.get("g%d" % i) else "нет" for i in (1, 2, 3, 4))))
    return "\n".join(out)


def main():
    sel, e8, ref, e4, c4 = load("select68.json"), load("e8-68.json"), load("ref68.json"), load("e4-lines.json"), load("counts68-e4.json")
    print(rr_line("`L_base`", sel["base"]["L"]))
    print()
    print(rr_line("`S_base`", sel["base"]["S"]))
    if e8:
        m = e8["rules"]["main"]
        print()
        print(rr_line("E8 (основное правило)", dict(rr=m["rr"])))
    print("\n#### Ответ по гипотезам\n\n| гипотеза | лонг | шорт |\n|---|---|---|")
    for h in sel["outcomes"]["L"]:
        print("| %s | %s | %s |" % (h, sel["outcomes"]["L"][h], sel["outcomes"]["S"][h]))
    print("\n#### Клетки E5 / EP (Холм m = %d, первый шаг %.5f; шумовая планка max|z| %.2f)\n" % (sel["m"]["E"], 0.05 / sel["m"]["E"], sel["noise"]["E"][0]))
    print(cells_table(sel, "E"))
    print("\n#### Клетки B (Холм m = %d, первый шаг %.5f; шумовая планка max|z| %.2f)\n" % (sel["m"]["B"], 0.05 / sel["m"]["B"], sel["noise"]["B"][0]))
    print(cells_table(sel, "B"))
    for s in ("L", "S"):
        for cid, t in sel.get("labels_b", {}).get(s, {}).items():
            print("\n- %s `%s`: %s" % (s, cid, t))
    if e8:
        print("\n#### E8 — разворот после `rt` шорта (194 события)\n\n| правило | n | Σ $ | $/сделку | Sharpe | месяцы (сделок: Σ $) | 90 % интервал $/сделку | «база + E8» Σ $ | Sharpe | макс. одновр. |\n|---|---:|---:|---:|---:|---|---|---:|---:|---:|")
        for k, x in e8["rules"].items():
            s_ = x["slice"]
            print("| %s | %d | %s | %s | %.2f | %s | [%s; %s] | %s | %.2f | %d |" % (k, x["n"], f(s_["sum"], 1), f(s_["mean"], 3), s_["sharpe"], months(s_["months"]), f(s_["lo90"]) if s_["lo90"] is not None else "—",
                  f(s_["hi90"]) if s_["hi90"] is not None else "—", f(x["with_sbase"]["sum"], 1), x["with_sbase"]["sharpe"], x["with_sbase"]["max_concurrent"]))
        ln = e8["rules"]["main"]["line"]
        print("\nЛиния E8: среднее суточное %s $ ± %.3f (z %s), p %.4f; l1 %s, l2 %s, l3 %s, l4 %s, l5 %s." % (f(ln["mean_day"], 3), ln["se_day"], f(ln["z"]), ln["p"], ln["l1_first_step"], ln["l2"], ln["l3"], ln["l4"], ln["l5"]))
    if ref:
        print("\n#### Справочно к `L_n24` (без Холма)\n\n| клетка поверх `L_n24` | n | Σ $ | $/сделку | Sharpe | ΔΣ к `L_n24` ± SE | z |\n|---|---:|---:|---:|---:|---|---:|")
        b = ref["ref"]
        print("| **`L_n24`** | %d | %s | %s | %.2f | | |" % (b["n"], f(b["sum"], 1), f(b["mean"], 3), b["sharpe"]))
        for cid, c in ref["cells"].items():
            st, d = c["stats"], c["delta"]
            print("| %s | %d | %s | %s | %.2f | %s ± %.1f | %s |" % (cid, st["n"], f(st["sum"], 1), f(st["mean"], 3), st["sharpe"], f(d["dsum"], 1), d["se_sum"], f(d["z"])))
    if e4:
        print("\n#### E4 / 45 — новые источники уровней (линия; Холм m_L = %d, шумовая планка max|z| %.2f)\n\n| источник × сторона | n | Σ $ | $/сделку | Sharpe | месяцы (сделок: Σ $) | R:R | стопов | убыток стопа ср. $ (%% маржи) | среднее суточное $ ± SE | z | p | l1 | l2 | l3 | l4 | l5 | ответ |\n|---|---:|---:|---:|---:|---|---:|---:|---|---|---:|---:|---|---|---|---|---|---|" % (e4["m_L"], e4["noise_bar_z"]))
        for nm, r in e4["cells"].items():
            st, ln = r["stats"], r["line"]
            print("| %s (%s) | %d | %s | %s | %.2f | %s | %.2f | %d | %s (%.2f %%) | %s ± %.3f | %s | %.4f | %s | %s | %s | %s | %s | %s |" % (
                nm, SRC[nm[0]], st["n"], f(st["sum"], 1), f(st["mean"], 3), st["sharpe"], months(st["months"]), st["rr"]["rr"], st["rr"]["stops"]["n"], f(st["rr"]["stops"]["mean_loss"]),
                st["rr"]["stops"]["mean_loss_pct"], f(ln["mean_day"], 3), ln["se_day"], f(ln["z"]), ln["p"], ln["l1"], ln["l2"], ln["l3"], ln["l4"], ln["l5"], ln["outcome"]))


if __name__ == "__main__":
    main()
