#!/bin/bash
# Ход долгой задачи для экрана владельца (tools/pulse/pulse.py): пишет /data/progress/<job>.json атомарно (tmp + mv).
# Использование: progress.sh <job> <ticket> <step> <done> <total> [unit] [next]
#   progress.sh tk042-dl TK-042 докачка 61 97 сут "проверка сентября → проверка старых эпох"
# Звать на каждом шаге и не реже раза в минуту. Файл свежее 30 мин попадает на экран; готово — done == total.
set -eu
if [ $# -lt 5 ]; then echo "usage: $0 <job> <ticket> <step> <done> <total> [unit] [next]" >&2; exit 2; fi
job=$1 ticket=$2 step=$3 done_=$4 total=$5 unit=${6:-} next=${7:-}
case $job in ''|*[!A-Za-z0-9._-]*) echo "job: только A-Za-z0-9._-" >&2; exit 2;; esac
num='^[0-9]+([.][0-9]+)?$'
[[ $done_ =~ $num && $total =~ $num ]] || { echo "done/total: числа" >&2; exit 2; }
dir=/data/progress
mkdir -p "$dir"
esc() { printf '%s' "$1" | tr -d '\n\r\t' | sed 's/\\/\\\\/g; s/"/\\"/g'; }
tmp="$dir/.$job.json.tmp.$$"
printf '{"ticket":"%s","step":"%s","done":%s,"total":%s,"unit":"%s","next":"%s","updated":"%s"}\n' \
  "$(esc "$ticket")" "$(esc "$step")" "$done_" "$total" "$(esc "$unit")" "$(esc "$next")" "$(date -Iseconds)" > "$tmp"
mv -f "$tmp" "$dir/$job.json"
