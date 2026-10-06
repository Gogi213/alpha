#!/bin/bash
# TK-071: обёртка benchrun/benchrun2 поверх alsched (демон alpha-sched.service). benchrun-sched.sh <wave|stand> <команда…>
# Замер идёт юнитом демона в эксклюзивном окне (производство на freeze); env вызывающего переносится файлом; учёт дисков (io-acct)
# и снимок реестра (TK-068) — внутри окна (benchrun-inner.sh). Демон молчит → прежний замок: /data/sched/benchrun2-legacy.sh.
cls=$1
case $cls in wave|stand) ;; *) echo "benchrun: класс wave|stand" >&2; exit 2;; esac
S=${SCHED_DIR:-/data/sched}
hb=$(stat -c %Y "$S/heartbeat" 2>/dev/null || echo 0)
if [ $(( $(date +%s) - hb )) -gt 30 ]; then exec "$S/benchrun2-legacy.sh" "$@"; fi
shift
mkdir -p "$S/env"; E="$S/env/$$.sh"
export -p | grep -Ev '^declare -x (INVOCATION_ID|JOURNAL_STREAM|SYSTEMD_EXEC_PID|MANAGERPID|NOTIFY_SOCKET|MEMORY_PRESSURE_[A-Z_]*|OLDPWD|PWD|SHLVL|_|LISTEN_[A-Z]*|BASH_[A-Z_]*|BASHOPTS|SHELLOPTS|PPID|UID|EUID)=' > "$E"
trap 'rm -f "$E"' EXIT
python3 "$S/alsched.py" "$cls" --max-runtime "${BENCH_MAX_RUNTIME:-2h}" bash "$S/benchrun-inner.sh" "$E" "$cls" "$@"
