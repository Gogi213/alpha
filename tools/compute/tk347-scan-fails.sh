#!/usr/bin/env bash
# TK-347: какие прошлые прогоны молча роняли «Order id exists» (drive.rs:263) — обход логов счёта на calc (под alsched, не вручную по ssh).
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk347-scan --max-runtime 1h --cores 1 --mem 4 -- bash /data/tk0347/tk347-scan-fails.sh
# Выход: /data/tk0347/scan2/{control,hits,fails}.txt, summary.txt. Свой каталог /data/tk0347/ НЕ обходится: первая версия
# грепала собственный fails.txt и дописывала в него же — 446 ГБ, /data заполнен (22.10).
# Первый проход (*.log, до tk0345 в порядке глоба) дал 0 попаданий; здесь — хвост глоба с tk0345 и все текстовые расширения.
set -uo pipefail
O=/data/tk0347/scan2; mkdir -p "$O"; : > "$O/control.txt"; : > "$O/hits.txt"; : > "$O/fails.txt"
G=(-rIls --include='*.log' --include='*.out' --include='*.err' --include='*.txt')
# контроль: известные падения MYX 02-13/02-01 лежат в tk0345 — обход обязан их найти
nice -n 10 ionice -c3 grep "${G[@]}" "Order id exists" /data/tk0345/ >> "$O/control.txt" 2>/dev/null
go=0
for d in /data/*/; do
  [ "$d" = /data/tk0345/ ] && go=1
  [ "$go" = 1 ] || continue
  case "$d" in /data/alpha/|/data/local-backup/|/data/sched/|/data/registry/|/data/tk0347/) continue;; esac
  nice -n 10 ionice -c3 grep "${G[@]}" "Order id exists" "$d" 2>/dev/null >> "$O/hits.txt"
  nice -n 10 ionice -c3 find "$d" \( -name 'fail*.txt' -o -name units.txt \) -type f 2>/dev/null | while read -r f; do
    grep -H -E 'GRID_FAIL| rc=[1-9]' "$f" 2>/dev/null | sed 's/^/FAIL /' | head -n 2000
  done | head -n 200000 >> "$O/fails.txt"
done
{ echo "control: $(wc -l < "$O/control.txt")"; echo "hits: $(wc -l < "$O/hits.txt")"; echo "fail-строк: $(wc -l < "$O/fails.txt")"; } > "$O/summary.txt"
cat "$O/summary.txt"
