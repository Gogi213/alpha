#!/usr/bin/env bash
# TK-347: какие прошлые прогоны молча роняли «Order id exists» (drive.rs:263) — обход логов счёта на calc (под alsched, не вручную по ssh).
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk347-scan --max-runtime 1h --cores 1 --mem 4 -- bash /data/tk0347/tk347-scan-fails.sh
# Выход: /data/tk0347/scan/hits.txt (файлы лога с ошибкой), fails.txt (строки GRID_FAIL / rc≠0 в units.txt), summary.txt.
set -uo pipefail
O=/data/tk0347/scan; mkdir -p "$O"; : > "$O/hits.txt"; : > "$O/fails.txt"
for d in /data/*/; do
  case "$d" in /data/alpha/|/data/local-backup/|/data/sched/|/data/registry/) continue;; esac
  nice -n 10 ionice -c3 grep -rIl --include='*.log' "Order id exists" "$d" 2>/dev/null >> "$O/hits.txt"
  nice -n 10 ionice -c3 find "$d" \( -name 'fail*.txt' -o -name units.txt \) -type f 2>/dev/null | while read -r f; do
    grep -H -E 'GRID_FAIL| rc=[1-9]' "$f" 2>/dev/null | sed 's/^/FAIL /' >> "$O/fails.txt"
  done
done
{ echo "hits: $(wc -l < "$O/hits.txt")"; echo "fail-строк: $(wc -l < "$O/fails.txt")"; } > "$O/summary.txt"
cat "$O/summary.txt"
