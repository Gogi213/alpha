#!/usr/bin/env python3
"""PreToolUse (Bash/PowerShell): отказ голому `systemd-run … CPUQuota` на сервере счёта (TK-133 С-49, правило 06.10).

Новое производство — только `python3 /data/sched/alsched.py submit …` (пакование ядер, бюджет памяти); замеры —
`/data/benchrun.sh`. Голый systemd-run мимо демона ломает учёт ядер (сумма заданий ≤ 1500 %).
Обход: `alsched` или `benchrun` в той же команде (submit/обёртка), либо переменная ALPHA_BARE_SYSTEMD_RUN_OK=1."""
import json, os, re, sys

HOST = os.environ.get("RPV_GUARD_HEAVY_HOST", "89.163.242.211")
ON_CALC = re.compile(re.escape(HOST) + r"|(?:^|[\s@'\"])calc(?:$|[\s'\"])")
RUN = re.compile(r"(?:^|[\s;&|(`'\"])systemd-run\s")
QUOTA = re.compile(r"CPUQuota", re.I)
MSG = ("Голый systemd-run … CPUQuota на сервере счёта запрещён (правило 06.10 17:40, С-49): новое производство — "
       "python3 /data/sched/alsched.py submit --cls prod --name N --max-runtime T --cores N --mem G -- команда; "
       "замеры — /data/benchrun.sh wave|stand.")


def verdict(cmd):
    """None — пропустить; строка — причина отказа."""
    if os.environ.get("ALPHA_BARE_SYSTEMD_RUN_OK") == "1":
        return None
    if not (RUN.search(cmd) and QUOTA.search(cmd) and ON_CALC.search(cmd)):
        return None
    if "alsched" in cmd or "benchrun" in cmd:
        return None
    return MSG


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    if d.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    why = verdict((d.get("tool_input") or {}).get("command") or "")
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
