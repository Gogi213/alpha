#!/usr/bin/env python3
"""Замер точности судей TypeSafe на размеченных случаях (аудит дизайна 22.09 §1: «точность ни одного
судьи не измерена, порог 0,5 не проверен»). Случаи — из истории проекта (реальные сигнатуры ошибок
прогонов 19–22.09, строки предрегистрации в духе `docs/plan/runs.csv`), метка — известный ответ.
Вопросы задаются **теми же формулировками**, что в боевых судьях (`prereg-guard.py`, `err-classify.py`),
поэтому число — точность именно боевого вопроса, а не похожего.

    python tools/typesafe/eval-judges.py [--json docs/findings/jev-eval-<дата>.json]

Печатает по каждому случаю вероятность/выбор и итог: доля верных при пороге 0,5 и худший случай.
Ключ — `TYPESAFE_API_KEY`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_HERE, os.path.join(_HERE, "..", "compute")]
from judge import Judge  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _load(name: str, file: str):
    for base in (os.path.join(_HERE, "..", "compute"), _HERE):
        path = os.path.join(base, file)
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(file)


# R3: у каждого числового параметра есть ссылка на В-## или замер (метка True/False) и названа ли
# одна главная гипотеза. Параметры и ссылки — настоящие для проекта (В-65, В-80, M16, В-58 п. 5).
PREREG = [
    ("ladder3x2..20w2 (M16), stop pct2 (В-65), take 1to1 (В-65), deadline 7200 (В-80), ttl 1800 (В-80), "
     "band 20 (В-80), queue prob:3 (В-80); set a45-bid age=2700 (В-70); hypothesis H1: long after BTC 4h drop",
     True, True),
    ("ladder3x2..20 stop pct1.5 take 1to1 deadline 5400 ttl 900 band 30; set a45-bid", False, False),
    ("single@fr, stop before (В-65), take 1to1 (В-65), early exit 1s/2s/3s (В-58 п. 5), deadline 600 (В-38); "
     "hypothesis: prilipanie exit improves net over deadline-only", True, True),
    ("single@fr, stop before (В-65), take 1to1, early exit 5s, deadline 600 (В-38); exploring exits", False, False),
    ("frozen live branch: ladder3x2..20w2 (M16), pct2 (В-65), 1to1 (В-65), 7200 (В-80), ttl1800 (В-80), band 20 "
     "(В-80), prob:3 (В-80), D=20 (M15); single hypothesis: edge over same coin-day drift > 0 on days from 23.09",
     True, True),
    ("eat35 exit, gone60 exit, stop pct2 (В-65); many axes: age, flow, side, regime", False, False),
    ("nightly base: stop before|at|behind|midfr|stack2|pct0.5|pct1|pct2 (В-65), take 1to1 (В-65), deadlines "
     "60/600/3600/7200 (В-38), ttl wall (В-74), band 20 (В-80)", True, False),
    ("queue prob:4.5, band 25 bps, ttl 1200 s; hypothesis: tighter band helps", False, True),
]

# Классы ошибок — реальные хвосты логов прогонов проекта.
ERRORS = [
    ("Error: order status is invalid to proceed the request\nbounce-grid: прогон остановлен", "invalid_order_status"),
    ("error: the following required arguments were not provided:\n  --queue-model <QUEUE_MODEL>\n\nUsage: alpha lob bounce-grid", "flag_refused"),
    ("Error: условие «цена ушла из полосы» (F5, В-74) требует --band-exit-bps: полоса — число замера, умолчания нет", "flag_refused"),
    ("alpha-grid-nightly-2026-09-18-base.service: A process of this unit has been killed by the OOM killer.\nKilled", "oom"),
    ("Error: Os { code: 28, kind: StorageFull, message: \"No space left on device\" }", "disk_full"),
    ("Error: кэш подходов не годится: нет study/approaches/D20/2026-09-21/approaches-HYPEUSDT.csv (touches-from)", "cache_missing"),
    ("bounce-grid: HYPEUSDT пропущен — нет маркера verify-HYPEUSDT.status == ok (K1), --allow-unverified для отладки", "k1_marker"),
    ("thread 'main' panicked at src/lob/strategy.rs:1234:17:\nattempt to subtract with overflow\nnote: run with RUST_BACKTRACE=1", "new"),
    ("warning: field `x` is never read\nbounce-grid: HYPEUSDT готов — суток 6, касаний 132117, 9.2s", "none"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()
    guard = _load("prereg_guard", "prereg-guard.py")
    errc = _load("err_classify", "err-classify.py")
    j = Judge()
    out: dict = {"prereg": [], "errors": []}

    ok_ns = ok_hn = 0
    for row, want_ns, want_hn in PREREG:
        ans = guard.judge_prereg(j, row)
        ns, hn = ans["numbers_sourced"]["noul"], ans["hypothesis_named"]["noul"]
        ok_ns += (ns >= 0.5) == want_ns
        ok_hn += (hn >= 0.5) == want_hn
        out["prereg"].append({"row": row, "want_numbers": want_ns, "p_numbers": ns, "want_hyp": want_hn, "p_hyp": hn})
        print(f"R3 числа {'✓' if (ns >= 0.5) == want_ns else '✗'} p={ns:.2f} (ждём {want_ns}); "
              f"гипотеза {'✓' if (hn >= 0.5) == want_hn else '✗'} p={hn:.2f} (ждём {want_hn}) — {row[:70]}")

    known_text = "\n".join(f"- {k}: {rx} → {act}" for k, (rx, act) in errc.KNOWN.items())
    ok_cls = 0
    for tail, want in ERRORS:
        state = ("Проект alpha, лог прогона бэктеста (lob bounce-grid / touches / verdict). Известные классы "
                 "ошибок и что делать:\n" + known_text + "\n\nСовпадения по сигнатурам (regex): не проверялись\n\n"
                 "Хвост лога:\n" + tail)
        ans = j.ask(state, {"cls": Judge.choice(
            "Класс ошибки",
            {**{k: act for k, (_, act) in errc.KNOWN.items()},
             "new": "новый вид ошибки, сигнатур нет — нужен разбор",
             "none": "ошибок по сути нет (шум, предупреждение)"})})
        got = ans["cls"]["choice"]
        ok_cls += got == want
        out["errors"].append({"tail": tail, "want": want, "got": got, "probabilities": ans["cls"].get("probabilities")})
        print(f"класс {'✓' if got == want else '✗'} {got} (ждём {want}, концентрация {ans['cls']['confidence']:.2f}) — {tail.splitlines()[0][:70]}")

    n_p, n_e = len(PREREG), len(ERRORS)
    summary = {
        "model": j.model_version,
        "r3_numbers_acc": ok_ns / n_p, "r3_hypothesis_acc": ok_hn / n_p, "err_class_acc": ok_cls / n_e,
        "n_prereg": n_p, "n_errors": n_e, "usage": j.usage,
    }
    out["summary"] = summary
    print(f"ИТОГ {j.model_version}: R3 «числа со ссылками» {ok_ns}/{n_p}, R3 «гипотеза названа» {ok_hn}/{n_p}, "
          f"класс ошибки {ok_cls}/{n_e} (порог 0,5)")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
