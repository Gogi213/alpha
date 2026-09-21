#!/usr/bin/env python3
"""Чтение ночи (В-81, TypeSafe в мета-контуре): одна строка «ок / смотреть / сломано» с
причиной — для `ALERTS.log`, Telegram и утреннего ритуала, вместо grep-эвристик.

Код собирает факты ночи (`study/ALERTS.log` за эту ночь, строки `study/verdicts.csv`
за неё, хвост `study/nightly-<день>.log`, `systemctl --failed`, диск), TypeSafe даёт
типизированное суждение: статус, класс причины, нужен ли владелец. Ключ —
`TYPESAFE_API_KEY` из окружения (на счётной — `/etc/alpha/typesafe.env`).

    python3 bin/nightly-read.py --night 2026-09-22 [--study study] [--json]

Выход 0 — ок; 1 — смотреть/сломано; 2 — чтение недоступно (нет ключа/сети). Печать —
одна строка `<ts> nightly-<день>: ЧТЕНИЕ <статус> — <причина>; владелец: да/нет`.
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
    "пишет вердикты. Норма: в ALERTS.log одна строка «ОК», в verdicts.csv строка на каждый набор; "
    "вердикты «мало данных»/«красный» — обычный результат (альфы пока нет), это НЕ поломка. "
    "Поломка — ночь пропущена, сетка/вердикт упали, диск полон, юнит в failed. «Смотреть» — ночь "
    "прошла, но есть неожиданность: вердикт «зелёный», резкий рост/падение кругов, новый вид ошибки."
)


def tail(path: str, n: int) -> str:
    if not os.path.exists(path):
        return f"(нет файла {path})"
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return "\n".join(lines[-n:])


def night_alerts(path: str, night: str) -> str:
    if not os.path.exists(path):
        return "(нет ALERTS.log)"
    with open(path, encoding="utf-8", errors="replace") as f:
        rows = [l for l in f.read().splitlines() if f"nightly-{night}:" in l]
    return "\n".join(rows[-20:]) or "(строк этой ночи нет)"


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

    try:
        j = Judge()
    except MissingKey as e:
        print(f"nightly-read: {e}")
        return 2

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
    try:
        ans = j.ask(
            state,
            {
                "status": Judge.choice(
                    "Как прошла ночь?",
                    {
                        "ok": "Ночь прошла штатно: сетки и вердикты посчитаны, поломок нет; «мало данных»/«красный» — норма",
                        "look": "Ночь прошла, но есть неожиданность, которую стоит посмотреть утром (зелёный вердикт, резкий сдвиг чисел, новый вид предупреждения, диск близок к пределу)",
                        "broken": "Ночь не состоялась или сломалась: пропуск, падение сетки/вердикта, юнит failed, диск полон",
                    },
                ),
                "cause": Judge.choice(
                    "Главная причина статуса (для ok — none)",
                    {
                        "none": "поломок и неожиданностей нет",
                        "skipped": "ночь пропущена гейтом «сетка ещё идёт» или таймером",
                        "grid_failed": "сетка упала (ошибка бэктеста, память, файл)",
                        "verdict_failed": "вердикт не посчитался",
                        "disk": "диск полон или почти полон",
                        "unit_failed": "юнит systemd в состоянии failed",
                        "green_verdict": "появился вердикт «зелёный» — надо читать",
                        "shift": "числа кругов/точки резко отличаются от прежних ночей",
                        "other": "иное",
                    },
                ),
                "owner_needed": Judge.noul(
                    "Нужно ли решение владельца (не исполнителя), чтобы двигаться дальше — например диск, ключи, доступ?"
                ),
            },
        )
    except RuntimeError as e:
        print(f"nightly-read: {e}")
        return 2

    status = ans["status"]["choice"]
    cause = ans["cause"]["choice"]
    owner = ans["owner_needed"]["noul"]
    label = {"ok": "ОК", "look": "СМОТРЕТЬ", "broken": "СЛОМАНО"}[status]
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(
        f"{ts} nightly-{night}: ЧТЕНИЕ {label} ({ans['status']['confidence']:.2f}) — причина {cause} "
        f"({ans['cause']['confidence']:.2f}); владелец: {'да' if owner >= 0.5 else 'нет'} ({owner:.2f})"
    )
    if a.json:
        print(json.dumps({"answers": ans, "usage": j.usage}, ensure_ascii=False, indent=1))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
