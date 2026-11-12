#!/usr/bin/env bash
# Обёртка одного задания очереди (`gridq.sh` запускает её юнитом systemd): рабочий каталог, команда с логом, итог
# `<rc> <пик памяти, байт> <oom_kill>` рядом с заданием в running/<id>.rc — пик и OOM берутся из cgroup самого юнита.
set -u
JOB="$1"
# shellcheck disable=SC1090
source "$JOB"
cd "$JOB_HOME" || { echo "127 0 0" > "${JOB%.job}.rc"; exit 127; }
mkdir -p "$(dirname "$JOB_LOG")"
"${JOB_CMD[@]}" > "$JOB_LOG" 2>&1
rc=$?
cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
peak=$(cat "$cg/memory.peak" 2>/dev/null || echo 0)
oom=$(awk '$1 == "oom_kill" {print $2}' "$cg/memory.events" 2>/dev/null || echo 0)
echo "$rc $peak ${oom:-0}" > "${JOB%.job}.rc.tmp" && mv "${JOB%.job}.rc.tmp" "${JOB%.job}.rc"
exit "$rc"
