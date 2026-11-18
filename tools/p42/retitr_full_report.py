#!/usr/bin/env python3
"""П-42 §13.24 (TK-599): отчёт по-русски и файл заморозки из результатов автомата (`results.pkl` драйвера `retitr_full_run.py`).

  python3 retitr_full_report.py <results.pkl> --md docs/findings/p42-retitration-full-2026-11-18.md --frozen docs/research/freeze/tk599-frozen.json [--commit <SHA кода>]

Отчёт: область и отступления, ворота (G1–G3b, G5), шаг 0 (уровень баз и сборок C TK-595 по месяцам, измеренный σ сделки), по стороне — проход 1, проход 2, совместная сетка,
заморозка, m; проверка вперёд (четыре свёртки, склейка 105 сут., охрана суммы, расхождение выборов, собственное среднее, «сторона выключена» — часть 1); исход заморозки.
Файл заморозки — конфигурация стороны, которая читается на 17–30.11 (коммитится отдельно ДО чтения). Чтение 17–30.11 здесь не делается и не печатается.
Тесты — tools/p42/tests/test_retitr_full_run.py (мир `World` из test_retitr_full.py).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pickle
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402

MONTH_RU = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
SIDE_RU = {"L": "лонг", "S": "шорт"}
FROZEN_VERSION = "tk599-frozen-1"
SCOPE_LIMIT = ("Ограничение области: сочетания двух и более признаков не проверялись (движок v21 — один фильтр на вариант); "
               "вывод „признаки F не улучшают сетап“ относится только к одному признаку на конфигурацию.")
DEVIATIONS = (
    "метрика отбора, проверки вперёд и чтения — средний $ на сделку, а не $/сут. (слово владельца «пробои не про количество а про качество сделки»);",
    "`rf` стоит в группе 2 «выходы» (в плане — среди фильтров);",
    "у `natr_min` добавлены соседи 0,2 и 0,8 для плато; у `e` лонга значение 0,2, у `e` шорта расширение 0,5 (правки критика 18.11);",
    "признаки без данных на всём окне не титруются (ворота G5), `depth_mean_bp`, `depth_min_bp` исключены;",
    "метрики OI за 17.06–17.07 докачаны (данные, не P&L); вместо единого кэша — три окна `wa/wb/wc` с перекрытием;",
    "§13.24.2: группа 6 — одна категориальная ось `F`, один фильтр признака на вариант (запрет движка v21, а не выбор после просмотра).",
)


def esc(x):
    return str(x).replace("|", "/").replace("\n", " ")


def f2(x, nd=2):
    return "—" if x is None or x != x else ("%+.*f" % (nd, x))


def f_(x, nd=2):
    return "—" if x is None or x != x else ("%.*f" % (nd, x))


def cfg_text(cfg):
    return ", ".join("%s=%s" % (a, c["id"]) for a, c in cfg.items()) if cfg else "база"


def level_rows(ser):
    """По месяцам и итог: [(название, Σ$, сделок, $/сделку, $/сут.)] — ряд склеенного окна (gross − fees)."""
    S, N = rf.day_sums(ser)
    rows = []
    for k in range(len(rf.MONTH_LEN)):
        d = rf.month_days([k])
        s, n = float(S[d].sum()), int(N[d].sum())
        rows.append((MONTH_RU[k], s, n, s / n if n else float("nan"), s / len(d)))
    s, n = float(S.sum()), int(N.sum())
    rows.append(("всего", s, n, s / n if n else float("nan"), s / rf.NDAYS))
    return rows


def trade_sigma(ser):
    return float(np.std(ser.val, ddof=1)) if len(ser) > 1 else float("nan")


def step_row(st):
    d = next((r["d"] for r in st.rows if r["id"] == st.winner), None)
    z = st.stat.z if st.stat is not None else None
    se = st.stat.se if st.stat is not None else None
    return "| %d | `%s` | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        st.group, st.axis, st.mode, esc(st.cur or "база"), esc(st.winner or "—"), f2(d, 3), f_(se, 3), f_(z, 2), "да" if st.accepted else "нет",
        esc("; ".join(st.reasons)[:220]) if st.reasons else "")


STEP_HEAD = ("| группа | ось | режим | было | победитель | Δ $/сделку | SE | z | принят | причины отказа |\n"
             "|---|---|---|---|---|---|---|---|---|---|")


def render_steps(title, steps):
    out = ["**%s** (клеток: %d)" % (title, sum(s.n_cells for s in steps)), "", STEP_HEAD]
    out += [step_row(s) for s in steps]
    return out + [""]


def render_joint(jt):
    if not jt.axes:
        return ["Совместная сетка: принятых осей нет — не строилась.", ""]
    out = ["Совместная сетка: оси %s, клеток %d, против базы стороны, категориальная форма (z ≥ 2,5)." % (", ".join("`%s`" % a for a in jt.axes), jt.n_cells), "",
           "| конфигурация | Δ $/сделку | прошла | не прошла по |", "|---|---|---|---|"]
    for c in jt.cells:
        out.append("| %s | %s | %s | %s |" % (esc(cfg_text(c["cfg"])), f2(c["d"], 3), "да" if c["passed"] else "нет", esc(", ".join(c["fails"]))))
    out += ["", "Итог совместной сетки: **%s**." % (cfg_text(jt.chosen) if jt.chosen is not None else "прошедшей клетки нет — конфигурация прохода 1"), ""]
    return out


def render_outcome(o):
    out = ["#### %s: пять месяцев (полное окно, 136 сут.)" % SIDE_RU[o.side], ""]
    out += render_steps("Проход 1", o.steps1)
    out += ["Конфигурация после прохода 1: **%s**." % cfg_text(o.cfg1), ""]
    out += render_steps("Проход 2", o.steps2)
    out += ["Проход 2 %s." % ("сошёлся (шагов не принято)" if o.converged else "НЕ сошёлся: принят хотя бы один шаг ⇒ заморозка = база")]
    out += [""] + render_joint(o.joint)
    out += ["Конфигурация процедуры на пяти месяцах: **%s**; m (клеток обоих проходов, расширений, совместной сетки) = **%d**." % (cfg_text(o.final), o.m), ""]
    return out


def render_gates(g, g5=None):
    out = ["## Ворота", ""]
    if not g:
        return out + ["Ворота не передавались в отчёт.", ""]
    g12 = g.get("G12", {})
    out.append("- G1/G2 (бинарь, монеты, сетки, манифесты): %s." % ("пройдены" if not g12.get("fail") else "НЕ пройдены: " + ", ".join(g12["fail"])))
    for s, v in g.get("G3", {}).items():
        out.append("- G3 «окна без потерь», %s: различий на внутренней части перекрытий %d%s; G3b: сделок, закрытых обрезкой конца данных, в зачётных диапазонах кроме 13.11 — 0 "
                   "(на 13.11 у `wc` — %d)." % (SIDE_RU[s], len(v["diff"]), ("" if not v["diff"] else " — " + "; ".join(v["diff"][:3])), v["forced_ok"]))
    out += ["", "G5 — доля непустых значений признака у кандидатов потока базы стороны по месяцам (порог ≥ 95 % в каждом):", "",
            "| сторона | признак | " + " | ".join(MONTH_RU) + " | титруется |", "|---|---|" + "---|" * len(MONTH_RU) + "---|"]
    for s, d in (g.get("G5") or g5 or {}).items():
        for fa, v in d.items():
            out.append("| %s | `%s` | %s | %s |" % (SIDE_RU[s], fa, " | ".join("%.1f %%" % (100 * x) for x in v["shares"]), "да" if v["ok"] else "**снят**"))
    return out + [""]


def render_report(res, spec, meta=None):
    meta = meta or {}
    out = ["# П-42 §13.24: перетитровка всего сетапа на 01.07–13.11 (136 сут.) — отчёт TK-599", "",
           "Статус: счёт по §13.24 + §13.24.1 + §13.24.2 выполнен автоматом `tools/p42/retitr_full.py` (драйвер `retitr_full_run.py`), ручных решений после просмотра нет. "
           "Метрика везде — средний $ на сделку (gross − fees, без фандинга), Δ к эталону, блочный бутстреп по суткам (блок 4, %d повторов)." % res.get("nboot", rf.NBOOT),
           "Код: коммит `%s`; сетки sha256 `%s`; бинарь md5 `%s`." % (meta.get("commit", "?"), meta.get("grids_sha256", "?")[:16], meta.get("bin_md5", "?")),
           "**Чтение вперёд 17–30.11 здесь не сделано** — только после заморозки (отдельный коммит `docs/research/freeze/tk599-frozen.json`).", "",
           "## Область и отступления", "", "Отступления от плана (записаны в §13.24 / §13.24.2 до счёта):", ""]
    out += ["%d. %s" % (i, d) for i, d in enumerate(DEVIATIONS, 1)]
    out += ["", SCOPE_LIMIT, "",
            "Все пять месяцев окна уже видели на уровне баз (TK-595, TK-596) ⇒ число внутри окна — «найдено на этих данных»; доказательством служат только проверка вперёд (m = 1 на сторону) "
            "и чтение 17–30.11 (m = 2).", ""]
    out += render_gates(res.get("gates"), res.get("g5"))
    out += ["## Шаг 0: уровень баз и сборок C TK-595 по месяцам (описательно)", ""]
    for s in rf.SIDES:
        out += ["**%s**" % SIDE_RU[s], "", "| ряд | месяц | Σ $ | сделок | $/сделку | $/сут. |", "|---|---|---|---|---|---|"]
        for k, label in (("base", "%s_base" % s), ("C", "%s_C (TK-595)" % s)):
            for name, sm, n, per, pd_ in level_rows(res["step0"][s][k]):
                out.append("| %s | %s | %s | %d | %s | %s |" % (label, name, f2(sm), n, f2(per, 3), f2(pd_)))
        sg = trade_sigma(res["step0"][s]["base"])
        out += ["", "Измеренный σ сделки базы %s на окне: %.2f $ (n = %d) — заменяет допущение 5,7 / 4,1 $ в расчёте мощности §13.24." % (SIDE_RU[s], sg, len(res["step0"][s]["base"])), ""]
    out += ["## Процедура на пяти месяцах", ""]
    for s in rf.SIDES:
        out += render_outcome(res["outcomes"]["%s:full" % s])
    out += ["## Проверка процедуры вперёд (расширяющееся окно, 105 сут.)", ""]
    for s in rf.SIDES:
        wf = res["wf"][s]
        out += ["#### %s" % SIDE_RU[s], "", "| обучение → проверка | выбор свёртки | проход 2 сошёлся | m |", "|---|---|---|---|"]
        for i, (tr, te) in enumerate(rf.FOLDS, start=1):
            o = res["outcomes"]["%s:f%d" % (s, i)]
            out.append("| %s → %s | %s | %s | %d |" % (", ".join(MONTH_RU[k] for k in tr), MONTH_RU[te], esc(cfg_text(o.final)), "да" if o.converged else "нет", o.m))
        st, own = wf["stat"], res["own_wf"][s]
        out += ["", "Склейка четырёх месяцев проверки (%d сут.): Δ среднего $/сделку «конфигурация свёртки − база» = **%s**, SE %s, z %s, нижняя граница (одност. 5 %%, m = 1) **%s**; "
                "ΣS выбранных %s, ΔΣ %s, SE_Σ %s; охрана суммы: %s." % (wf["n_days"], f2(st.d, 3), f_(st.se, 3), f_(st.z, 2), f2(st.lower, 3), f2(st.s_c), f2(st.dsum), f_(st.se_sum),
                                                                       "выполнена" if wf["guard"] else "НЕ выполнена"),
                "Процедура вперёд: **%s**." % ("подтверждена" if wf["confirmed"] else "не подтверждена"),
                "Расхождение выборов между свёртками и полным окном: %s." % (esc(wf["diverge"]) if wf["diverge"] else "нет"),
                "Собственное среднее конфигураций свёрток на склейке: %s $/сделку, нижняя граница %s, p = %.3f, без двух лучших суток %s, без двух лучших монет %s ⇒ **%s** "
                "(«сторона выключена», часть 1: %s)." % (f2(own["stat"].mean, 3), f2(own["stat"].lower, 3), own["stat"].p, f2(own["no_best_days"], 3), f2(own["no_best_coins"], 3),
                                                      own["verdict"], "не подтверждено" if own["verdict"] != "подтверждено" else "подтверждено"),
                "m на сторону (четыре свёртки + полное окно) = %d." % wf["m"], ""]
    out += ["## Исход заморозки (до чтения 17–30.11)", "", "| сторона | заморожено | основание |", "|---|---|---|"]
    for s in rf.SIDES:
        dec, note = res["freeze"][s]
        cfg = {} if dec == "база" else res["outcomes"]["%s:full" % s].final
        out.append("| %s | %s (`%s`) | %s |" % (SIDE_RU[s], "база" if dec == "база" else "C*: " + cfg_text(cfg), esc(rf.cfg_keys(spec, s, cfg)), esc(note)))
    out += ["", "Правило исходов — §13.24 «Исходы»: Δ вперёд ≤ 0 или проход 2 не сошёлся ⇒ база; Δ > 0 без подтверждения или с расхождением выборов ⇒ C* с пометкой; "
            "«сторона выключена» решается после чтения 17–30.11.", ""]
    return "\n".join(out) + "\n"


def frozen_json(res, spec, meta=None):
    """Файл заморозки: что читается на 17–30.11 по каждой стороне (ключи движка), с исходом и пометкой."""
    sides = {}
    for s in rf.SIDES:
        dec, note = res["freeze"][s]
        wf = res["wf"][s]
        full = res["outcomes"]["%s:full" % s]
        cfg = {} if dec == "база" else full.final
        st, own = wf["stat"], res["own_wf"][s]
        sides[s] = {"decision": dec, "note": note, "axes": {a: c["id"] for a, c in cfg.items()}, "patches": {a: c["patch"] for a, c in cfg.items()},
                    "keys": rf.cfg_keys(spec, s, cfg), "base_keys": rf.cfg_keys(spec, s, {}), "converged_full": bool(full.converged), "m": int(wf["m"]),
                    "folds": wf["folds"], "diverge": wf["diverge"], "confirmed": bool(wf["confirmed"]),
                    "forward": {k: float(getattr(st, k)) for k in ("d", "se", "z", "lower", "dsum", "se_sum", "s_c", "s_r")},
                    "own_wf": {"verdict": own["verdict"], "mean": float(own["stat"].mean), "lower": float(own["stat"].lower), "p": float(own["stat"].p)}}
    return {"version": FROZEN_VERSION, "protocol": "П-42 §13.24 + §13.24.1 + §13.24.2", "meta": meta or {}, "scope": SCOPE_LIMIT, "sides": sides}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--md", required=True)
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--commit", default="?")
    ap.add_argument("--grids", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk599-grids.json"))
    a = ap.parse_args(argv)
    res = pickle.load(open(a.results, "rb"))
    spec = rf.Spec.load(a.grids)
    import hashlib
    meta = {"commit": a.commit, "grids_sha256": hashlib.sha256(open(a.grids, "rb").read()).hexdigest(), "bin_md5": "a03c91df5a70c8eb32cbb7122943b8f9",
            "date": datetime.date.today().isoformat(), "nboot": res.get("nboot", rf.NBOOT)}
    with open(a.md, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_report(res, spec, meta))
    with open(a.frozen, "w", encoding="utf-8", newline="\n") as f:
        json.dump(frozen_json(res, spec, meta), f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("отчёт:", a.md, "; заморозка:", a.frozen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
