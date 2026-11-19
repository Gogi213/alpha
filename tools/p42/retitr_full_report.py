#!/usr/bin/env python3
"""П-42 §13.24 (TK-599): отчёт по-русски и файл заморозки из результатов автомата (`results.pkl` драйвера `retitr_full_run.py`).

  python3 retitr_full_report.py <results.pkl> --md docs/findings/p42-retitration-full-2026-11-18.md --frozen docs/research/freeze/tk599-frozen.json [--commit <SHA кода>] [--npz /data/p42-tk599/npz] [--ftab /data/p42-tk599/ftab]

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

LEGACY_BIN_MD5 = "a03c91df5a70c8eb32cbb7122943b8f9"      # серия без поля `bin_md5` посчитана бинарём §13.24 (поле пишется со сменой бинаря, §13.24.6)
NPZ_DEFAULT = "/data/p42-tk599/npz"
FTAB_DEFAULT = "/data/p42-tk599/ftab"
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
    "метрики OI за 17.06–17.07 докачаны (данные, не P&L); вместо единого кэша — три окна `wa/wb/wc` с перекрытием; геометрия окон §13.24.3 заменена §13.24.4 (G3 остановил счёт на `ROBOUSDT`): "
    "глубина предыстории H = 32 суток у `wb`, `wc`; у `wa` — 14 (данных до 17.06 нет), зачёт `wa` до 18.07 воротами и проверкой сходимости не доказывается — свёртка 1 проверки вперёд (подбор на июле) и июльский зачёт частично опираются на эту недоказуемую голову;",
    "§13.24.2: группа 6 — одна категориальная ось `F`, один фильтр признака на вариант (запрет движка v21, а не выбор после просмотра).",
)


SCOPE_T24 = ("Ось `t24` (группа 5, порог оборота 24 ч) не титровалась: ни у одной клетки нет минимума сделок по месяцам — ни на одной стороне, ни в одном из проходов (строка «(а)» в таблицах шагов); "
             "вывод «`t24` не влияет» не делается.")
PROTOCOL_NOTE = ("Поле `protocol` в файле заморозки («П-42 §13.24 + §13.24.1 + §13.24.2») — метка формата файла; прогон выполнен по §13.24 с §13.24.1–13.24.8. Файл заморожен коммитом и не переписывается.")
ASSUMED_SIGMA = {"L": 5.7, "S": 4.1}             # §13.24 «Модель»: σ сделки (лонг / шорт), $ — допущение до шага 0
MDE_READ = {"L": 2.1, "S": 1.7}                   # §13.24 «Модель»: чтение 17–30.11, MDE при Холме на 2, $/сделку
MDE_FWD = {"L": 0.95, "S": 0.76}                  # §13.24 «Модель»: проверка вперёд 105 сут., наименьшая различимая Δ (одност. 5 %, мощность 80 %), $/сделку
N_READ = {"L": 60, "S": 50}                       # §13.24 «Модель»: сделок на чтении 17–30.11
Z_MDE = 1.96 + 0.84                               # Холм на 2 (z 1,96) + мощность 80 % (z 0,84): MDE = Z_MDE · σ / √n


def filtercheck_summary(path):
    """Сводка `log/filtercheck.tsv` (§13.24.8 п. 5, условие 2 критика): строк, различных потоков, строк с расхождением (кандидатов ≠ строк таблицы, отброшено / прошло движком ≠ Python, no_key ≠ 0)."""
    rows, bad, streams = 0, 0, set()
    for ln in open(path, encoding="utf-8").read().splitlines():
        f = ln.split("\t")
        if len(f) != 12:
            continue
        rows += 1
        streams.add(f[2])
        c, r, de, dp, ke, kp, nk = (int(x) for x in f[5:12])
        bad += int(not (c == r and de == dp and ke == kp and nk == 0))
    return {"rows": rows, "bad": bad, "streams": len(streams)}


def late_deviations(meta):
    """Отступления, записанные в протокол после §13.24.2 (но до чтения 17–30.11): пункты 7+ списка отступлений."""
    fc = meta.get("filtercheck")
    fc_text = ("сверка счётчиков фильтра движка со счётом Python по таблице потока: %d строк (%d потоков), расхождений %d, `no_key` = 0 везде" % (fc["rows"], fc["streams"], fc["bad"])
               if fc and not fc["bad"] else ("сверка счётчиков фильтра: **расхождения**, см. `log/filtercheck.tsv`" if fc else "сверка счётчиков фильтра: файл не приложен"))
    return (
        "(7) §13.24.3: торговое окно движка — 60 суток (`COUNT_DAYS = 60`), а не 80; геометрия окон заменена до счёта, число суток (136), месяцы, сетки и правила прежние (сетка торговых суток 14…73);",
        "(8) §13.24.4: геометрия §13.24.3 заменена (глубина предыстории H = 32 суток после остановки ворот G3 на `ROBOUSDT`; подробности — п. 5);",
        "(9) §13.24.4, оплошность: после остановки ворот G3 на `ROBOUSDT` показаны два числа `net_total` баз на `wc` прежней геометрии; решения на них не опираются, прежние `out/`, `var/` удалены, счёт — с нуля;",
        "§13.24.5 (добавление, не отступление): к основному прогону добавлена чувствительность порога минимума сделок N ∈ {10, 15, 20, 25} (слово владельца 18.11: «также нужно протитровать минимальное количество сделок от 10-15-20-25»); "
        "вердикт, заморозка и чтение 17–30.11 — только при N = 20, строки N ≠ 20 описательные; `sens` запускается после коммита заморозки;",
        "(10) §13.24.6: бинарь движка `a03c91df` заменён бинарём «только скорость» `a112684a` (склад суток сделок, кэш сигналов, включаются ключами командной строки; семантика, сетки, правила не меняются): серий по бинарям — %s; "
        "ворота до смены (§13.24.6 п. 2) — по записи TK-607 и координатора, пост-фактум аудит (п. 4) — в разделе ниже;" % bin_text(meta),
        "(11) §13.24.7: сборщик признаков группы 6 — Rust `alpha-feat` вместо Python `tk594_feat.py` (по приказу владельца 19.11, до завершения ворот G-A2/G-A3; ворота пройдены, PASS критика, условность снята); "
        "с 19.11 01:02:17 UTC — v3 `04714685…` вместо v2 `f65d9583…` (по «go» координатора, ворота TK-609). Решение координатора: пост-фактум аудит таблиц пересборкой Python не проводится — равенство доказано воротами G-A3 (байт в байт) и теневой пересборкой первой таблицы; "
        "таблицы по окнам — по сборщикам (три сборщика в одном прогоне, пересборок нет): %s;" % builders_text(meta),
        "(12) §13.24.8: таблицы признаков — по потокам, без union (ключ `(sym, sig_ms)` не уникален между потоками; остановка охраны 19.11 00:45 UTC); перекрёстная охрана — только для столбцов, чистых функций ключа; "
        "затронутых шагов 0 из 323 (в тексте протокола стояло «303» — устарело, повтор по кэшу на остановке дал 323 шага); %s." % fc_text,
    )


def mde_lines(res):
    """Различимость с измеренным σ сделки (до чтения 17–30.11): модель §13.24 линейна по σ при тех же числах сделок — старые значения масштабируются на σ_изм / σ_доп; рядом прямой расчёт MDE = 2,8·σ/√n."""
    out = ["### Различимость с измеренным σ (до чтения 17–30.11)", "",
           "| сторона | σ допущ. | σ измер. | чтение 17–30.11 (≈ %d / %d сделок): MDE при Холме на 2, было → стало ($/сделку) | прямой расчёт 2,8·σ/√n | проверка вперёд 105 сут.: наименьшая различимая Δ, было → стало |" % (N_READ["L"], N_READ["S"]),
           "|---|---|---|---|---|---|"]
    for s in rf.SIDES:
        sg = trade_sigma(res["step0"][s]["base"])
        k = sg / ASSUMED_SIGMA[s]
        out.append("| %s | %.1f | %.2f | %.1f → %.2f | %.2f | %.2f → %.2f |" % (SIDE_RU[s], ASSUMED_SIGMA[s], sg, MDE_READ[s], MDE_READ[s] * k, Z_MDE * sg / N_READ[s] ** 0.5, MDE_FWD[s], MDE_FWD[s] * k))
    return out + ["", "Вывод: чтение 17–30.11 различит только эффект порядка 2,9 $/сделку на лонге и ≈ 2,0 на шорте — больше уровня баз августа; ожидаемый исход прежний — «не ясно» (§13.24 «Модель»).", ""]


def chrono_text(meta):
    c = meta.get("chrono")
    if not c:
        return ""
    return "Хронология: заморозка закоммичена `%s` в %s; `sens` запущен %s — после заморозки (§13.24.5 п. 7, условие 1)." % (c["freeze_sha"], c["freeze_at"], c["sens_start"])


def render_audit(a):
    """Раздел «Пост-фактум аудит бинаря движка» (§13.24.6 п. 4) по `audit.json` (`tk599_posthoc.py`)."""
    s = a["summary"]
    why = {c["vid"]: c for c in a["cells"]}
    n_fixed = sum(1 for c in a["cells"] if any(w.startswith(("итог", "победитель")) for w in c["why"]))
    n_rand = sum(1 for c in a["cells"] if any(w.startswith("случайная") for w in c["why"]))
    out = ["## Пост-фактум аудит бинаря движка (§13.24.6 п. 4)", "",
           "Бинарь `a03c91df` (md5 `%s`, sha256 `%s…`) пересчитал по всем трём окнам (%s) %d клеток: итог полной процедуры и победитель каждой из четырёх свёрток каждой стороны (%d различных клеток), "
           "%d случайных клеток из серий, посчитанных новым бинарём (зерно — sha256 файла результатов процедуры `%s…`), и 1 контрольную клетку старого бинаря (старый против старого — проверка самого сравнения). "
           "Сравнивались побайтно (`cmp`) `{long,short}-{trades,daily,coinday,entryrej}.csv` и `meta.json` с выходами основного прогона `out/<окно>/<vid>`. Серий в кэше по бинарям: %s." % (
               a["old_bin_md5"], a["old_bin_sha256"][:16], ", ".join(s["windows"]), s["cells"], n_fixed, n_rand, a["results_sha256"][:16],
               ", ".join("%s (%d)" % (m, n) for m, n in sorted(a["series_by_bin"].items()))), "",
           ("**Итог: сравнено файлов %d, различий 0 — прогон не обнуляется.**" % s["files_compared"]) if s["diffs"] == 0 else
           ("**Итог: сравнено файлов %d, РАЗЛИЧИЙ %d — прогон обнуляется (§13.24.6 п. 4), повтор на `a03c91df`; решает координатор.**" % (s["files_compared"], s["diffs"])), "",
           "| клетка | сторона | зачем | бинарь серии в кэше | файлов | различий |", "|---|---|---|---|---|---|"]
    for vid in sorted(a["compared"]):
        c = why[vid]
        files = sum(1 for r in a["compared"][vid].values() for k in r if k != "meta_diff")
        diffs = [(w, k) for w, r in a["compared"][vid].items() for k, v in r.items() if k != "meta_diff" and v != "равны"]
        out.append("| `%s` | %s | %s | `%s` | %d | %s |" % (vid, c["side"] or "—", esc("; ".join(c["why"])), (c["bin_md5"] or "?")[:8], files, "0" if not diffs else esc(", ".join("%s %s" % d for d in diffs))))
    return out + [""]


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
        out.append("- G3 «окна без потерь» (зачётный кусок перекрытия), %s: различий %d%s; проверка сходимости (окна с T0 на 7 суток раньше): различий %d%s; G3b: сделок, закрытых обрезкой конца данных, "
                   "в зачётных диапазонах кроме 13.11 — 0 (на 13.11 у `wc` — %d)." % (SIDE_RU[s], len(v["diff"]), ("" if not v["diff"] else " — " + "; ".join(v["diff"][:3])),
                                                                                       len(v.get("conv", [])), ("" if not v.get("conv") else " — " + "; ".join(v["conv"][:3])), v["forced_ok"]))
        for k, r in (v.get("ramp") or {}).items():
            out.append("  - диагностика %s, %s: сделок %d, различий %d%s." % (k, SIDE_RU[s], r["n_trades"], r["n_diff"], ("" if not r.get("by_day") else " по суткам " + ", ".join("%s: %d" % kv for kv in r["by_day"].items()))))
    out += ["", "G5 — доля непустых значений признака у кандидатов потока базы стороны по месяцам (порог ≥ 95 % в каждом):", "",
            "| сторона | признак | " + " | ".join(MONTH_RU) + " | титруется |", "|---|---|" + "---|" * len(MONTH_RU) + "---|"]
    for s, d in (g.get("G5") or g5 or {}).items():
        for fa, v in d.items():
            out.append("| %s | `%s` | %s | %s |" % (SIDE_RU[s], fa, " | ".join("%.1f %%" % (100 * x) for x in v["shares"]), "да" if v["ok"] else "**снят**"))
    return out + [""]


def ids_text(ids):
    return ", ".join("%s=%s" % kv for kv in sorted(ids.items())) if ids else "база"


def tot_text(t):
    return "%d / %s / %s" % (t["n"], f2(t["sum"]), f2(t["per_trade"], 3))


def render_sens(sens):
    """Раздел «Чувствительность порога минимума сделок» (§13.24.5): описательные таблицы по N на сторону; вердикт и заморозка — N = 20. `sens` — `results/sens.json` (ключи N — строки)."""
    ns = sorted(int(n) for n in sens["by_n"])
    ctl = sens.get("control") or {}
    out = ["## Чувствительность порога минимума сделок (§13.24.5; описательно)", "",
           "Слова владельца 18.11 (дословно): «также нужно протитровать минимальное количество сделок от 10-15-20-25». Это чувствительность, не перевыбор: порог 20 назначен до счёта; вердикт, заморозка "
           "и чтение 17–30.11 — только конфигурации N = 20 (основной прогон); строки N ≠ 20 описательные, «лучший N» не выбирается, любое превосходство строки N ≠ 20 над N = 20 — «найдено на этих данных». "
           "При каждом N заново прошла вся процедура на 136 сут. (проходы 1–2, совместная сетка, четыре свёртки, полная); 17–30.11 не читалось.",
           "Порог месяца при N: %s (июль, август, сентябрь, октябрь, 01–13.11 = 0,4·N)." % "; ".join("N = %d — %s" % (n, "/".join(str(x) for x in sens["min_trades"][str(n)])) for n in ns),
           "Контроль N = 20: %s; запусков движка в контроле: %s. Файл заморозки (sha256): `%s`." % (
               "совпал с `results.pkl` основного прогона (число различий 0)" if ctl.get("equal") else "**НЕ совпал** (%s)" % esc("; ".join(ctl.get("diff", [])[:3])) if ctl else "не выполнялся",
               ctl.get("engine_variants", "—"), (sens.get("meta") or {}).get("frozen_sha256", "?")[:16]), ""]
    for s in rf.SIDES:
        out += ["#### %s" % SIDE_RU[s], "",
                "| N | итог (принятые клетки) | проход 2 сошёлся | Δ вперёд $/сделку | SE | z | нижняя гр. | подтверждена | собственный вердикт | исход заморозки | m_N | клеток / выбыло по (а) |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n in ns:
            r = sens["by_n"][str(n)][s]
            f = r["forward"]
            out.append("| %d%s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %d / %d |" % (
                n, " (контроль, вердикт)" if n == rf.CONTROL_N else "", esc(ids_text(r["full"])), "да" if r["converged_full"] else "нет", f2(f["d"], 3), f_(f["se"], 3), f_(f["z"], 2),
                f2(f["lower"], 3), "да" if r["confirmed"] else "нет", r["own_wf"]["verdict"], esc(r["freeze"][0] + ": " + r["freeze"][1]), r["m"], r["cells"]["total"], r["cells"]["dropped_a"]))
        out += ["", "| N | проверка вперёд (105 сут.): итог — сделок / Σ $ / $/сделку | база | полное окно (136 сут.): итог | база | выборы свёрток 1–4 |", "|---|---|---|---|---|---|"]
        for n in ns:
            r = sens["by_n"][str(n)][s]
            out.append("| %d | %s | %s | %s | %s | %s |" % (n, tot_text(r["fwd_totals"]["final"]), tot_text(r["fwd_totals"]["base"]), tot_text(r["full_totals"]["final"]),
                                                          tot_text(r["full_totals"]["base"]), esc(" ; ".join("ф%d: %s" % (i, ids_text(x)) for i, x in enumerate(r["folds"], 1)))))
        ref = sens["by_n"][str(rf.CONTROL_N)][s]["full"] if str(rf.CONTROL_N) in sens["by_n"] else None
        diffs = []
        for n in ns:
            if n == rf.CONTROL_N or ref is None:
                continue
            cur = sens["by_n"][str(n)][s]["full"]
            d = ["`%s`: %s → %s" % (a, ref.get(a, "база"), cur.get(a, "база")) for a in sorted(set(ref) | set(cur)) if ref.get(a) != cur.get(a)]
            diffs.append("N = %d: %s" % (n, "; ".join(d) if d else "итог тот же, что при N = 20"))
        out += ["", "Итог при N ≠ 20 против N = 20 (N = 20 → N): " + (" | ".join(diffs) if diffs else "—") + ".",
                "Испытания: m_сем(%s) = Σ_N m_N = %d (проверок вперёд в семействе — %d на сторону; вердикт — N = 20, m = 1 проверка вперёд, m = 2 чтение 17–30.11)." % (
                    SIDE_RU[s], sum(sens["by_n"][str(n)][s]["m"] for n in ns), len(ns)), ""]
    return out


def render_report(res, spec, meta=None):
    meta = meta or {}
    out = ["# П-42 §13.24: перетитровка всего сетапа на 01.07–13.11 (136 сут.) — отчёт TK-599", "",
           "Статус: счёт по §13.24 с §13.24.1–13.24.8 выполнен автоматом `tools/p42/retitr_full.py` (драйвер `retitr_full_run.py`), ручных решений после просмотра нет. "
           "Метрика везде — средний $ на сделку (gross − fees, без фандинга), Δ к эталону, блочный бутстреп по суткам (блок 4, %d повторов)." % res.get("nboot", rf.NBOOT),
           "Код: коммит `%s`; сетки sha256 `%s`; бинари движка md5 (по сериям кэша) %s." % (meta.get("commit", "?"), meta.get("grids_sha256", "?")[:16], bin_text(meta)),
           "Таблицы признаков по сборщикам (python / alpha-feat md5; все три побайтно равны по воротам G-A3, §13.24.7): %s." % builders_text(meta),
           chrono_text(meta),
           "**Чтение вперёд 17–30.11 здесь не сделано** — только после заморозки (отдельный коммит `docs/research/freeze/tk599-frozen.json`).", "",
           "## Область и отступления", "", "Отступления от плана (записаны в протокол до счёта и до чтения 17–30.11), п. 1–6 — §13.24–13.24.2:", ""]
    out += ["%d. %s" % (i, d) for i, d in enumerate(DEVIATIONS, 1)]
    out += ["", "Записано в протокол позже (в скобках — номера отступлений протокола):", ""] + ["- " + d for d in late_deviations(meta)]
    out += ["", PROTOCOL_NOTE, "", SCOPE_LIMIT, "", SCOPE_T24, "",
            "Все пять месяцев окна уже видели на уровне баз (TK-595, TK-596) ⇒ число внутри окна — «найдено на этих данных»; доказательством служат только проверка вперёд (m = 1 на сторону) "
            "и чтение 17–30.11 (m = 2).", ""]
    out += render_gates(res.get("gates"), res.get("g5"))
    if meta.get("audit"):
        out += render_audit(meta["audit"])
    out += ["## Шаг 0: уровень баз и сборок C TK-595 по месяцам (описательно)", ""]
    for s in rf.SIDES:
        out += ["**%s**" % SIDE_RU[s], "", "| ряд | месяц | Σ $ | сделок | $/сделку | $/сут. |", "|---|---|---|---|---|---|"]
        for k, label in (("base", "%s_base" % s), ("C", "%s_C (TK-595)" % s)):
            for name, sm, n, per, pd_ in level_rows(res["step0"][s][k]):
                out.append("| %s | %s | %s | %d | %s | %s |" % (label, name, f2(sm), n, f2(per, 3), f2(pd_)))
        sg = trade_sigma(res["step0"][s]["base"])
        out += ["", "Измеренный σ сделки базы %s на окне: %.2f $ (n = %d) — заменяет допущение 5,7 / 4,1 $ в расчёте мощности §13.24." % (SIDE_RU[s], sg, len(res["step0"][s]["base"])), ""]
    out += mde_lines(res)
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
    if meta.get("sens"):
        out += render_sens(meta["sens"])
    return "\n".join(out) + "\n"


def engine_md5s(npz_dir):
    """{md5 бинаря движка: число серий} по кэшу `npz` прогона: у серии поле `bin_md5` (TK-607, §13.24.6); серия без поля — `LEGACY_BIN_MD5`. Оборванные `.tmp.npz` не считаются."""
    out = {}
    for name in sorted(os.listdir(npz_dir)):
        if not name.endswith(".npz") or name.endswith(".tmp.npz"):
            continue
        with np.load(os.path.join(npz_dir, name)) as z:
            md5 = str(z["bin_md5"]) if "bin_md5" in z.files else LEGACY_BIN_MD5
        out[md5] = out.get(md5, 0) + 1
    return out


def ftab_builders(ftab_dir):
    """({окно: {сборщик: число таблиц потоков}}, [окно/vid таблиц с чужой меткой]) по `ftab/<окно>/<vid>/ftab.csv` (§13.24.7–8, приказ координатора 19.11: смешанный набор — как собран).
    Сборщик — по файлу признаков, который реально посчитал таблицу (`tk594-flow|deriv-<S|L>.parquet.info.json`): `engine` + `bin_md5` → `alpha-feat:<md5>`, иначе `python`.
    Метка `built-by.json` (кто вызвал сборку) может отличаться: сборка переиспользует готовый файл признаков, — такие таблицы перечислены вторым значением."""
    counts, relabeled = {}, []
    for w in sorted(os.listdir(ftab_dir)):
        wd = os.path.join(ftab_dir, w)
        if not os.path.isdir(wd):
            continue
        for vid in sorted(os.listdir(wd)):
            d = os.path.join(wd, vid)
            if vid == "ready" or not os.path.exists(os.path.join(d, "ftab.csv")):
                continue
            who = "python"
            for n in sorted(os.listdir(d)):
                if n.startswith("tk594-") and n.endswith(".parquet.info.json"):
                    info = json.load(open(os.path.join(d, n), encoding="utf-8"))
                    if info.get("engine") == "alpha-feat":
                        who = "alpha-feat:" + info["bin_md5"]
            bb = os.path.join(d, "built-by.json")
            if os.path.exists(bb) and "alpha-feat:" + json.load(open(bb, encoding="utf-8")).get("feat_bin_md5", "") != who:
                relabeled.append("%s/%s" % (w, vid))
            counts.setdefault(w, {})[who] = counts.get(w, {}).get(who, 0) + 1
    return counts, relabeled


def builders_text(meta):
    """Сборщики таблиц признаков для строки отчёта: «окно: сборщик N, …; …»."""
    b = meta.get("ftab_builders")
    if not b:
        return "?"
    s = "; ".join("%s: %s" % (w, ", ".join("%s %d" % (k.replace("alpha-feat:", "alpha-feat "), b[w][k]) for k in sorted(b[w]))) for w in sorted(b))
    rl = meta.get("ftab_relabeled") or []
    return s + ("; таблиц с меткой сборщика, не равной посчитавшему файл признаков: %d (%s)" % (len(rl), ", ".join(rl)) if rl else "")


def bin_text(meta):
    """Бинари движка для строки отчёта: «md5 (N серий), …»."""
    ser = meta.get("bin_md5_series")
    if not ser:
        return "?"
    return ", ".join("%s (%d сер.)" % (m, ser[m]) for m in sorted(ser))


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
    ap.add_argument("--sens", default="", help="results/sens.json (§13.24.5): добавляет раздел «Чувствительность порога минимума сделок»; в файл заморозки не попадает")
    ap.add_argument("--npz", default=NPZ_DEFAULT, help="каталог кэша серий прогона: md5 бинарей движка в мете заморозки читаются из серий (`bin_md5`), не зашиты")
    ap.add_argument("--audit", default="", help="audit.json пост-фактум аудита бинаря (`tk599_posthoc.py`, §13.24.6 п. 4): раздел отчёта; в файл заморозки не попадает")
    ap.add_argument("--filtercheck", default="", help="log/filtercheck.tsv (§13.24.8 п. 5): сводка сверки счётчиков фильтра в отступлении 12; в файл заморозки не попадает")
    ap.add_argument("--chrono", default="", help="«<SHA заморозки>|<время коммита заморозки>|<время старта sens>» — строка хронологии (§13.24.5 п. 7, условие 1); в файл заморозки не попадает")
    ap.add_argument("--ftab", default=FTAB_DEFAULT, help="каталог таблиц признаков прогона: сборщики таблиц по окнам пишутся в мету заморозки")
    ap.add_argument("--grids", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk599-grids.json"))
    a = ap.parse_args(argv)
    res = pickle.load(open(a.results, "rb"))
    spec = rf.Spec.load(a.grids)
    import hashlib
    if not os.path.isdir(a.npz):
        raise SystemExit("нет каталога кэша серий %s: укажите --npz — в мете заморозки обязаны быть md5 всех бинарей движка, использованных в прогоне" % a.npz)
    ser = engine_md5s(a.npz)
    if not ser:
        raise SystemExit("в %s нет серий .npz — md5 бинаря движка не определить" % a.npz)
    if not os.path.isdir(a.ftab):
        raise SystemExit("нет каталога таблиц признаков %s: укажите --ftab — в мете заморозки обязаны быть сборщики таблиц по окнам" % a.ftab)
    fb, relabeled = ftab_builders(a.ftab)
    meta = {"commit": a.commit, "grids_sha256": hashlib.sha256(open(a.grids, "rb").read()).hexdigest(), "bin_md5": sorted(ser), "bin_md5_series": ser,
            "ftab_builders": fb, "ftab_relabeled": relabeled, "npz_dir": os.path.abspath(a.npz), "date": datetime.date.today().isoformat(), "nboot": res.get("nboot", rf.NBOOT)}
    with open(a.md, "w", encoding="utf-8", newline="\n") as f:
        extra = {"sens": json.load(open(a.sens, encoding="utf-8")) if a.sens else None, "audit": json.load(open(a.audit, encoding="utf-8")) if a.audit else None,
                 "filtercheck": filtercheck_summary(a.filtercheck) if a.filtercheck else None}
        if a.chrono:
            extra["chrono"] = dict(zip(("freeze_sha", "freeze_at", "sens_start"), a.chrono.split("|")))
        f.write(render_report(res, spec, dict(meta, **extra)))
    with open(a.frozen, "w", encoding="utf-8", newline="\n") as f:
        json.dump(frozen_json(res, spec, meta), f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("отчёт:", a.md, "; заморозка:", a.frozen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
