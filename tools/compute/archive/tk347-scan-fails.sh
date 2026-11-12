#!/usr/bin/env bash
# TK-347: какие прошлые прогоны молча роняли «Order related to a given order id already exists» (drive.rs:263) —
# обход счёта на calc (под alsched, не вручную по ssh).
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk347-scan3 --max-runtime 4h --cores 1 --mem 4 -- bash /data/tk0347/tk347-scan-fails.sh
# v3 (22.10): v1/v2 грепали «Order id exists» — такой строки нет, настоящий текст ошибки в grid.log единицы:
# «Error: группа выходов: Order related to a given order id already exists»; контроль v2 дал 0 именно поэтому.
# Один проход find по именам grid.log / fail*.txt / units.txt (без чтения остального), один grep.
# Выход: /data/tk0347/scan3/{control,hits,fails}.txt, summary.txt. Свой каталог /data/tk0347/ НЕ обходится.
set -uo pipefail
O=/data/tk0347/scan3; mkdir -p "$O"; : > "$O/control.txt"; : > "$O/hits.txt"; : > "$O/fails.txt"
PAT='already exists'
# контроль: известное падение MYX 02-13 (tk0345/pass3) — обход обязан его найти; контроль = 0 → результат не годен
grep -H "$PAT" /data/tk0345/pass3/2026-02-13-MYXUSDT-F1/grid.log >> "$O/control.txt" 2>/dev/null
for d in /data/*/; do
  case "$d" in /data/alpha/|/data/local-backup/|/data/sched/|/data/registry/|/data/tk0347/|/data/lost+found/) continue;; esac
  nice -n 10 ionice -c3 find "$d" \( -name grid.log -o -name 'fail*.txt' -o -name units.txt \) -type f -print0 2>/dev/null \
    | xargs -0 -r grep -H -E "$PAT|GRID_FAIL| rc=[1-9]" 2>/dev/null | head -n 200000 > "$O/part.tmp"
  grep -- "$PAT" "$O/part.tmp" | sed -E 's/(.{240}).*/\1/' >> "$O/hits.txt"
  grep -v -- "$PAT" "$O/part.tmp" | sed 's/^/FAIL /' >> "$O/fails.txt"
  echo "$(date +%H:%M:%S) $d hits=$(wc -l < "$O/hits.txt") fails=$(wc -l < "$O/fails.txt")" >> "$O/progress.txt"
done
rm -f "$O/part.tmp"
{ echo "control: $(wc -l < "$O/control.txt")"; echo "hits: $(wc -l < "$O/hits.txt")"; echo "fail-строк: $(wc -l < "$O/fails.txt")"; } > "$O/summary.txt"
cat "$O/summary.txt"
