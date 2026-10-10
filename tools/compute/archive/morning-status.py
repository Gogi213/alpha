#!/usr/bin/env python3
"""Утренний статус машин (В-81 п. 7; граница кода и модели — аудит дизайна 22.09 §1): одна строка
«в норме / смотреть / сломано» по фактам обеих машин — диск, коллектор пишет, сверка прошла, перенос
прошёл, ночь прошла, кэши новых суток есть. Статус и причины решает **код** по прежним порогам
(диск 90/95 %, бинлог старше трёх сбросов, failed-юниты, «НЕТ» в строках суток, итог ночи); модель
(Jev) — только мнение «есть ли необычное вне правил» и «нужен ли владелец», с версией модели.

    python3 bin/morning-status.py [--collector ubuntu@139.99.91.22] [--json]

Выход 0 — в норме; 1 — смотреть/сломано. Без ключа статус печатается всё равно.
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
    "счётную машину, сверяются, считаются касания и сетки, пишется ALERTS.log. Правила статуса (диск, "
    "коллектор, сверка, перенос, кэш, ночь, failed-юниты) уже проверены кодом — нужен только взгляд на "
    "свободный текст фактов."
)

# Пороги статуса — прежний контракт утреннего статуса (В-81 п. 7; раньше жили в тексте для модели,
# аудит 22.09 §1 перенёс их в код): диск < 90 % — норма, 90–95 % — смотреть, ≥ 95 % — сломано.
DISK_LOOK_PCT = 90
DISK_BROKEN_PCT = 95
# Коллектор сбрасывает кадр на диск не реже раза в 10 с (CLAUDE.md, «олвейс-он»); три пропущенных
# сброса подряд — коллектор не пишет.
BINLOG_STALE_S = 3 * 10


def classify(facts: list[str]) -> tuple[str, list[str]]:
    """Статус утра кодом: (ok|look|broken, причины)."""
    broken: list[str] = []
    look: list[str] = []
    text = "\n".join(facts)
    for m in re.finditer(r"^(\S+): диск.*?(\d+)\s?%\s*занято", text, re.M):
        who, pct = m.group(1), int(m.group(2))
        if pct >= DISK_BROKEN_PCT:
            broken.append(f"диск {who} {pct} %")
        elif pct >= DISK_LOOK_PCT:
            look.append(f"диск {who} {pct} %")
    m = re.search(r"коллектор: юнит (\S+)", text)
    if m and m.group(1) != "active":
        broken.append(f"коллектор: юнит {m.group(1)}")
    if "коллектор: недоступен" in text:
        broken.append("коллектор недоступен по ssh")
    m = re.search(r"последний бинлог обновлён (\d+) с назад", text)
    if m and int(m.group(1)) > BINLOG_STALE_S:
        broken.append(f"бинлог не обновлялся {m.group(1)} с")
    if "сверка:" not in text:
        look.append("сверки нет")
    m = re.search(r"^перенос: .*$", text, re.M)
    if m and "done" not in m.group(0):
        look.append("перенос не дошёл до done")
    if re.search(r"вчерашние сутки \S+: в root — НЕТ", text):
        look.append("вчерашних суток нет в root")
    if re.search(r"касания — НЕТ", text):
        look.append("касания вчерашних суток не посчитаны")
    m = re.search(r"systemd failed: (.*)$", text, re.M)
    if m and m.group(1).strip() not in ("", "нет"):
        broken.append(f"failed-юниты: {m.group(1).strip()}")
    tail = [l for l in text.splitlines() if "nightly-" in l]
    if tail:
        last = tail[-1]
        if "ПРОВАЛ" in last or "СЛОМАНО" in last:
            broken.append("ночь сломана")
        elif "СМОТРЕТЬ" in last:
            look.append("ночь: СМОТРЕТЬ")
    if broken:
        return "broken", broken + look
    if look:
        return "look", look
    return "ok", []


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
    st, why = classify(facts)
    label = {"ok": "В НОРМЕ", "look": "СМОТРЕТЬ", "broken": "СЛОМАНО"}[st]
    opinion = ""
    ans = None
    try:
        j = Judge()
        ans = j.ask(
            CONTEXT + "\n\nФакты утра:\n" + text,
            {
                "unexpected": Judge.noul(
                    "Есть ли в фактах что-то необычное, не покрытое перечисленными правилами (новый вид "
                    "строки в тревогах, странное число)?"
                ),
                "owner_needed": Judge.noul("Нужно решение владельца (деньги, диск, доступ), а не действие исполнителя?"),
            },
        )
        opinion = (
            f"; мнение модели {j.model_version}: необычное p={ans['unexpected']['noul']:.2f}, "
            f"владелец p={ans['owner_needed']['noul']:.2f}"
        )
    except MissingKey:
        opinion = "; мнение модели: нет ключа"
    except RuntimeError as e:
        opinion = f"; мнение модели недоступно ({str(e)[:60]})"
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} УТРО: {label} — {'; '.join(why) if why else 'всё по правилам'} (код){opinion}")
    if a.json:
        print(json.dumps({"facts": facts, "status": st, "why": why, "answers": ans}, ensure_ascii=False, indent=1))
    return 0 if st == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
