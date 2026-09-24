#!/usr/bin/env bash
# Что сейчас идёт по проекту alpha — одной командой (владелец 24.09: «нужно как-то контролировать»):
# сборки на этой машине и на VPS, счёт на деке, диск коллектора, последние коммиты обеих веток.
#   bash tools/alpha-status.sh
set -uo pipefail
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=8 -o BatchMode=yes)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WAVE2="${WAVE2_TREE:-C:/visual projects/alpha-wave2}"

echo "== эта машина: сборки cargo"
n=$(tasklist //FO CSV //NH 2>/dev/null | grep -ciE '"(cargo|rustc)\.exe"')
echo "   процессов cargo/rustc: ${n:-0}"

echo "== VPS 13.140.29.171: сборки cargo"
ssh "${KEY[@]}" root@13.140.29.171 'ps -eo etimes,args --sort=-etimes | grep -E "[c]argo (build|test|clippy)" | head -3 | awk "{printf \"   %4d мин  \", \$1/60; \$1=\"\"; print substr(\$0,1,110)}"; pgrep -c -f "[r]ustc" | sed "s/^/   процессов rustc: /"' 2>/dev/null || echo "   недоступен"

echo "== дек: юниты счёта и загрузка"
ssh "${KEY[@]}" deck@192.168.1.49 'systemctl --user list-units "alpha-*" --state=running --no-legend 2>/dev/null | awk "{print \"   \" \$1}"; cat /proc/loadavg | awk "{print \"   нагрузка: \" \$1 \" \" \$2 \" \" \$3}"; df -h ~ | tail -1 | awk "{print \"   диск: \" \$5 \" занято, свободно \" \$4}"' 2>/dev/null || echo "   недоступен"

echo "== коллектор 139.99.91.22"
ssh "${KEY[@]}" ubuntu@139.99.91.22 'systemctl is-active alpha-collector | sed "s/^/   коллектор: /"; df -h /opt/alpha | tail -1 | awk "{print \"   диск: \" \$5 \" занято, свободно \" \$4}"
  # Наблюдение за соединениями (владелец 24.09: «ничего не рви, только следи»): монеты, чья запись за сегодня
  # не росла больше 5 мин. Одна-две — обычно неликвид; много разом — похоже на повисшее соединение.
  d=$(date -u +%F); cd /opt/alpha/root 2>/dev/null || exit 0
  # Только последняя часть монеты (-pN после перезапуска): закрытые части суток расти и не должны (24.09).
  latest=$(ls -t ./*-"$d"*.binlog 2>/dev/null | sed "s|^\./||" | awk -F"-$d" "!seen[\$1]++")
  all=$(printf "%s
" "$latest" | grep -c .); quiet=$(for f in $latest; do find "./$f" -mmin +5; done | sed "s|^\./||; s|-$d.*||" | sort -u)
  n=$(printf "%s" "$quiet" | grep -c .); echo "   молчат > 5 мин: $n из $all$( [ "$n" -gt 0 ] && echo ": $(echo $quiet | cut -c1-150)")"
  [ "$n" -ge 10 ] && echo "   ВНИМАНИЕ: много монет молчат разом — проверить соединения"; true' 2>/dev/null || echo "   недоступен"

echo "== коммиты"
git -C "$ROOT" log --format='   основная  %h %ad %s' --date=format:%H:%M -3 | cut -c1-120
[ -d "$WAVE2" ] && git -C "$WAVE2" log --format='   волна 2   %h %ad %s' --date=format:%H:%M -3 | cut -c1-120
git -C "$ROOT" status --short -- src vendor tools 2>/dev/null | head -5 | sed 's/^/   не закоммичено (основная): /'
[ -d "$WAVE2" ] && git -C "$WAVE2" status --short -- src vendor tools 2>/dev/null | head -5 | sed 's/^/   не закоммичено (волна 2): /'
exit 0
