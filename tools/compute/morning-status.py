#!/usr/bin/env python3
"""Утренний статус машин (В-81 п. 7, TypeSafe): одна строка «в норме / смотреть / сломано» по фактам
обеих машин — диск, коллектор пишет, сверка прошла, перенос прошёл, ночь прошла, кэши новых суток есть.
Факты собирает код (локально на счётной + по ssh к коллектору), судья решает, что из этого требует
человека. Заменяет утреннее чтение пяти логов руками.

    python3 bin/morning-status.py [--collector ubuntu@139.99.91.22] [--json]

Выход 0 — в норме; 1 — смотреть/сломано; 2 — суждение недоступно (факты всё равно печатаются).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def sh(cmd: list[str], timeout: int = 20, stdin: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", input=stdin)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"(ошибка: {e})"


def local_facts(study: str, root: str) -> list[str]:
    out = []
    du = shutil.disk_usage(".")
    out.append(f"счётная: диск свободно {du.free / 2**30:.1f} ГБ из {du.total / 2**30:.0f} ({100 * du.used / du.total:.0f} % занято)")
    days = sorted({m.group(1) for f in os.listdir(root) if (m := re.search(r"-(\d{4}-\d{2}-\d{2})", f))}) if os.path.isdir(root) else []
    out.append(f"счётная: сутки в root: {', '.join(days[-4:])} (всего {len(days)})")
    touches_dir = os.path.join(study, "touches")
    cached = sorted(d for d in os.listdir(touches_dir) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)) if os.path.isdir(touches_dir) else []
    done = [d for d in cached if os.path.exists(os.path.join(touches_dir, d, ".done"))]
    out.append(f"счётная: кэш касаний: {', '.join(cached[-4:])}; закрытых .done {len(done)}")
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    out.append(f"вчерашние сутки {yesterday}: в root — {'да' if yesterday in days else 'НЕТ'}, касания — {'да' if yesterday in cached else 'НЕТ'}")
    alerts = os.path.join(study, "ALERTS.log")
    if os.path.exists(alerts):
        with open(alerts, encoding="utf-8", errors="replace") as f:
            tail = [l for l in f.read().splitlines() if l.strip()][-6:]
        out.append("ночь (ALERTS.log, хвост):\n  " + "\n  ".join(tail))
    failed = sh(["systemctl", "--failed", "--no-legend", "--plain"])
    units = [l.split()[0] for l in failed.splitlines() if l.strip()]
    out.append(f"счётная: systemd failed: {', '.join(units) if units else 'нет'}")
    active = sh(["systemctl", "list-units", "alpha-grid-*", "--no-legend", "--plain"])
    running = [l.split()[0] for l in active.splitlines() if " running " in l or " start " in l]
    out.append(f"счётная: идущие сетки: {', '.join(running) if running else 'нет'}")
    return out


COMPUTE_SH = (
    "cd /opt/alpha-compute; df -h / | tail -1 | awk '{print \"счётная: диск \" $5 \" занято, свободно \" $4}'; "
    "echo \"счётная: сутки в root: $(ls root | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | sort -u | tail -4 | tr '\n' ' ')\"; "
    "echo \"счётная: кэш касаний: $(ls study/touches | grep -E '^20' | tail -4 | tr '\n' ' ')\"; "
    "y=$(date -u -d yesterday +%F); echo \"вчерашние сутки $y: в root — $(ls root | grep -q -- \"-$y\" && echo да || echo НЕТ), касания — $(test -d study/touches/$y && echo да || echo НЕТ)\"; "
    "echo 'ночь (ALERTS.log, хвост):'; tail -5 study/ALERTS.log | sed 's/^/  /'; "
    "echo \"счётная: systemd failed: $(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')\"; "
    "echo \"счётная: идущие сетки: $(systemctl list-units 'alpha-grid-*' --no-legend --plain | grep -E ' (running|start) ' | awk '{print $1}' | tr '\n' ' ')\""
)


def compute_facts_remote(collector: str, key: str | None, compute: str, compute_key: str) -> list[str]:
    """Факты счётной через прыжок коллектор → счётная (ключ к счётной лежит на коллекторе)."""
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"] + (["-i", key] if key else []) + [collector]
    # Скрипт — через stdin (`bash -s`) сквозь два прыжка: без экранирования кириллицы и кавычек.
    inner = f"ssh -o BatchMode=yes -o ConnectTimeout=10 -i {compute_key} {compute} bash -s"
    out = sh(ssh + [inner], timeout=60, stdin=COMPUTE_SH + "\n")
    return out.splitlines() if out else ["счётная: недоступна по ssh"]


def collector_facts(host: str, key: str | None) -> list[str]:
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"] + (["-i", key] if key else []) + [host]
    script = (
        "df -h /opt/alpha | tail -1 | awk '{print \"коллектор: диск \" $5 \" занято, свободно \" $4}'; "
        "systemctl is-active alpha-collector.service | sed 's/^/коллектор: юнит /'; "
        "f=$(ls -t /opt/alpha/root/*.binlog 2>/dev/null | head -1); "
        "[ -n \"$f\" ] && echo \"коллектор: последний бинлог обновлён $(( $(date +%s) - $(stat -c %Y \"$f\") )) с назад\"; "
        "v=$(ls -t /opt/alpha/verify/*.log 2>/dev/null | head -1); [ -n \"$v\" ] && echo \"сверка: $(basename $v .log): ok=$(grep -c 'status=ok' $v) fail=$(grep -c 'status=fail' $v)\"; "
        "s=$(ls -t /opt/alpha/sync/*.log 2>/dev/null | head -1); [ -n \"$s\" ] && echo \"перенос: $(basename $s .log): $(tail -1 $s | cut -c1-80)\"; "
        "grep -c reconnect /opt/alpha/root/gaps.csv 2>/dev/null | sed 's/^/коллектор: строк переподключений в gaps.csv: /'"
    )
    out = sh(ssh + [script], timeout=40)
    return out.splitlines() if out else ["коллектор: недоступен по ssh"]


CONTEXT = (
    "Проект alpha: коллектор (Bybit, 100 монет) пишет стакан на своём сервере; ночью сутки переносятся на "
    "счётную машину, сверяются (ok/fail по монетам, 6–10 fail — норма), считаются касания и сетки, пишется "
    "ALERTS.log. Норма утром: диск обеих машин < 90 %, коллектор active и бинлог обновлялся секунды назад, "
    "сверка вчерашних суток есть, перенос «done», касания вчерашних суток в кэше, в ALERTS последняя строка "
    "«ОК» или «ЧТЕНИЕ ОК», failed-юнитов нет. «Смотреть» — что-то отстаёт (нет вчерашних касаний, перенос не "
    "прошёл, диск 90–95 %, ЧТЕНИЕ СМОТРЕТЬ). «Сломано» — коллектор не пишет, диск ≥ 95 %, ночь сломана, юнит failed."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", default="study")
    ap.add_argument("--root", default="root")
    ap.add_argument("--collector", default="ubuntu@139.99.91.22")
    ap.add_argument("--collector-key", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--remote", action="store_true", help="снять факты счётной по ssh через коллектор (запуск с рабочей машины)")
    ap.add_argument("--compute", default="root@13.140.29.171")
    ap.add_argument("--compute-key", default="~/.ssh/id_compute_sync", help="ключ к счётной, лежащий на коллекторе")
    a = ap.parse_args()
    if a.remote:
        facts = compute_facts_remote(a.collector, a.collector_key, a.compute, a.compute_key) + collector_facts(a.collector, a.collector_key)
    else:
        facts = local_facts(a.study, a.root) + collector_facts(a.collector, a.collector_key)
    text = "\n".join(facts)
    print(text)
    try:
        j = Judge()
    except MissingKey as e:
        print(f"morning-status: {e}")
        return 2
    try:
        ans = j.ask(
            CONTEXT + "\n\nФакты утра:\n" + text,
            {
                "status": Judge.choice("Состояние машин и ночи", {"ok": "в норме", "look": "смотреть", "broken": "сломано"}),
                "worst": Judge.choice(
                    "Что хуже всего",
                    {"none": "ничего", "collector": "коллектор не пишет/юнит", "disk": "диск", "sync": "перенос не прошёл",
                     "verify": "сверки нет", "cache": "касания вчерашних суток не посчитаны", "night": "ночь сломана/пропущена", "unit": "failed-юнит"},
                ),
                "owner_needed": Judge.noul("Нужно решение владельца (деньги, диск, доступ), а не действие исполнителя?"),
            },
        )
    except RuntimeError as e:
        print(f"morning-status: {e}")
        return 2
    st = ans["status"]["choice"]
    label = {"ok": "В НОРМЕ", "look": "СМОТРЕТЬ", "broken": "СЛОМАНО"}[st]
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} УТРО: {label} ({ans['status']['confidence']:.2f}) — хуже всего: {ans['worst']['choice']} ({ans['worst']['confidence']:.2f}); владелец: {'да' if ans['owner_needed']['noul'] >= 0.5 else 'нет'} ({ans['owner_needed']['noul']:.2f})")
    if a.json:
        print(json.dumps({"facts": facts, "answers": ans}, ensure_ascii=False, indent=1))
    return 0 if st == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
