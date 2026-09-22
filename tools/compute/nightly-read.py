#!/usr/bin/env python3
"""Чтение ночи (В-81; граница кода и модели — аудит дизайна 22.09 §1): одна строка
«ок / смотреть / сломано» с причиной — для `ALERTS.log`, Telegram и утреннего ритуала.

Статус и причину решает **код** по фактам ночи: строки `study/ALERTS.log` этой ночи (итог «ОК»/
«ПРОВАЛ», «ночь пропущена», «не посчитался», «юнит failed»), строки `study/verdicts.csv` (есть ли,
есть ли «зелёный»), failed-юниты systemd. Раньше это решала модель по тем же фактам и печатала
в скобках `confidence` выбора — концентрацию распределения, похожую на вероятность верности.
Модель (Jev) теперь только **мнение** над свободным текстом хвоста лога: есть ли неожиданность,
не покрытая правилами, и нужен ли владелец; печатается с пометкой и версией модели. Без ключа
строка печатается всё равно (статус кодом), мнение пропускается.

    python3 bin/nightly-read.py --night 2026-09-22 [--study study] [--json]

Выход 0 — ок; 1 — смотреть/сломано. Печать — одна строка
`<ts> nightly-<день>: ЧТЕНИЕ <статус> — причина <причина> (код)[; мнение модели …]`.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

CONTEXT = (
    "Проект alpha: ночная сетка бэктестов (02:00 UTC) считает касания суток, гонит наборы форм, "
    "пишет вердикты. Вердикты «мало данных»/«красный» — обычный результат, не поломка. Правила "
    "статуса (пропуск, падение, failed-юнит, зелёный вердикт) уже проверены кодом — ниже нужен только "
    "взгляд на свободный текст лога."
)


def classify(alerts: list[str], verdict_rows: list[list[str]], failed_units: list[str]) -> tuple[str, str]:
    """Статус и причина ночи кодом: (ok|look|broken, причина)."""
    text = "\n".join(alerts)
    if any(u.startswith("alpha-grid-nightly") for u in failed_units):
        return "broken", "unit_failed"
    if "ночь пропущена" in text:
        return "broken", "skipped"
    if "не посчитался" in text:
        return "broken", "verdict_failed"
    if "сетка " in text and ("failed" in text or "ошибки в grid.err" in text or "ни одной готовой" in text):
        return "broken", "grid_failed"
    if "ПРОВАЛ" in text:
        return "broken", "other"
    if not verdict_rows:
        return "broken", "no_verdicts"
    if any(any("зелёный" in c for c in r) for r in verdict_rows):
        return "look", "green_verdict"
    return "ok", "none"


def tail(path: str, n: int) -> str:
    if not os.path.exists(path):
        return f"(нет файла {path})"
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return "\n".join(lines[-n:])


def night_alert_rows(path: str, night: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        # Свои прежние строки «ЧТЕНИЕ» в разбор не берём — иначе чтение читает само себя.
        return [l for l in f.read().splitlines() if f"nightly-{night}:" in l and "ЧТЕНИЕ" not in l]


def night_alerts(path: str, night: str) -> str:
    if not os.path.exists(path):
        return "(нет ALERTS.log)"
    return "\n".join(night_alert_rows(path, night)[-20:]) or "(строк этой ночи нет)"


def night_verdict_rows(path: str, night: str) -> list[list[str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return [r for r in csv.reader(f) if r and r[0] == night]


def failed_units() -> list[str]:
    """Failed-юниты системного и пользовательского менеджера (дек — пользовательские юниты)."""
    if not shutil.which("systemctl"):
        return []
    units: list[str] = []
    for scope in ([], ["--user"]):
        try:
            r = subprocess.run(["systemctl", *scope, "--failed", "--no-legend", "--plain"],
                               capture_output=True, text=True, timeout=10)
            units += [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    return units


def night_verdicts(path: str, night: str) -> str:
    if not os.path.exists(path):
        return "(нет verdicts.csv)"
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = [r for r in csv.reader(f) if r and r[0] == night]
    if not rows:
        return "(строк этой ночи нет)"
    return "\n".join(",".join(r) for r in rows[-60:])


def system_facts() -> str:
    out = []
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(
                ["systemctl", "--failed", "--no-legend", "--plain"],
                capture_output=True, text=True, timeout=10,
            )
            failed = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
            out.append("systemd failed: " + (", ".join(failed) if failed else "нет"))
        except (OSError, subprocess.SubprocessError):
            out.append("systemd failed: (не проверить)")
    try:
        du = shutil.disk_usage(".")
        out.append(f"диск: свободно {du.free / 2**30:.1f} ГБ из {du.total / 2**30:.0f} ({100 * du.used / du.total:.0f} % занято)")
    except OSError:
        pass
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--night", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--study", default="study")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    night = a.night
    state = "\n\n".join(
        [
            CONTEXT,
            f"Ночь: {night}",
            "ALERTS.log этой ночи:\n" + night_alerts(os.path.join(a.study, "ALERTS.log"), night),
            "verdicts.csv этой ночи (night,kind,verdict,form,rounds,point_bps,lower_bps):\n"
            + night_verdicts(os.path.join(a.study, "verdicts.csv"), night),
            "Хвост nightly-лога:\n" + tail(os.path.join(a.study, f"nightly-{night}.log"), 25),
            "Машина:\n" + system_facts(),
        ]
    )

    alerts = night_alert_rows(os.path.join(a.study, "ALERTS.log"), night)
    vrows = night_verdict_rows(os.path.join(a.study, "verdicts.csv"), night)
    status, cause = classify(alerts, vrows, failed_units())
    label = {"ok": "ОК", "look": "СМОТРЕТЬ", "broken": "СЛОМАНО"}[status]
    opinion = ""
    ans = None
    try:
        j = Judge()
        ans = j.ask(
            state,
            {
                "unexpected": Judge.noul(
                    "Есть ли в хвосте лога и строках тревог что-то неожиданное, чего не покрывают "
                    "перечисленные правила (новый вид ошибки, резкий сдвиг чисел кругов, странная строка)?"
                ),
                "owner_needed": Judge.noul(
                    "Нужно ли решение владельца (не исполнителя), чтобы двигаться дальше — например диск, ключи, доступ?"
                ),
            },
        )
        opinion = (
            f"; мнение модели {j.model_version}: неожиданность p={ans['unexpected']['noul']:.2f}, "
            f"владелец p={ans['owner_needed']['noul']:.2f}"
        )
    except MissingKey:
        opinion = "; мнение модели: нет ключа"
    except RuntimeError as e:
        opinion = f"; мнение модели недоступно ({str(e)[:60]})"
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} nightly-{night}: ЧТЕНИЕ {label} — причина {cause} (код){opinion}")
    if a.json:
        print(json.dumps({"status": status, "cause": cause, "answers": ans}, ensure_ascii=False, indent=1))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
