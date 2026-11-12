#!/bin/bash
# TK-381: докачка стакана фьючерсов Gate за 22.10 05–23 UTC (хвост TK-379). Запуск таймером раз в 2 ч (UTC, чётные часы :07).
# Проба BTC/ETH 22.10 05 UTC: 404 → строка в log/tk381.log, выход. 200 → все контракты (у кого есть файл 22.10 04) × часы 05–23,
# 8 потоков, готовое пропускается, gzip -t. Конец (всё скачано или срок) → log/tk381.done + остановка таймера.
# usage: tk381-gate-ob-retry.sh [корень=/srv/alpha-sda/tk379-ob]; DEADLINE_UTC=2026-10-25T20:00:00 (= 26.10 00:00 GMT+4).
set -u
root=${1:-/srv/alpha-sda/tk379-ob}; dir=$root/futures_usdt/orderbooks/202610; log=$root/log/tk381.log
TIMER=${TIMER:-tk381-gate-ob.timer}
deadline=$(date -u -d "${DEADLINE_UTC:-2026-10-25T20:00:00}" +%s)
exec 9>"$root/log/tk381.lock"; flock -n 9 || { echo "$(date -u +%FT%TZ) занято" >> "$log"; exit 0; }
[ -e "$root/log/tk381.done" ] && exit 0
url() { echo "https://download.gatedata.org/futures_usdt/orderbooks/${2:0:6}/$1-$2.csv.gz"; }
dl() {
  c=$1; h=$2; f=$3/$c-$h.csv.gz
  [ -s "$f" ] && return 0
  code=$(curl -s -m 600 --retry 6 --retry-delay 10 -o "$f.part381" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part381" 2>/dev/null; then mv "$f.part381" "$f"
  else rm -f "$f.part381"; printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss381.tsv"; fi
}
export -f dl
contracts=$root/log/tk381-contracts.txt
[ -s "$contracts" ] || ls "$dir" | grep -- '-2026102204\.csv\.gz$' | sed 's/-2026102204\.csv\.gz$//' | sort > "$contracts"
n=$(wc -l < "$contracts")
p1=$(curl -s -o /dev/null -m 60 -w '%{http_code}' "$(url BTC_USDT 2026102205)"); p2=$(curl -s -o /dev/null -m 60 -w '%{http_code}' "$(url ETH_USDT 2026102205)")
if [ "$p1" = 200 ] || [ "$p2" = 200 ]; then
  : > "$root/log/miss381.tsv"; tasks=$root/log/tk381-tasks.txt
  for h in $(seq -w 5 23); do while read -r c; do echo "$c 20261022$h"; done < "$contracts"; done > "$tasks"
  xargs -a "$tasks" -P8 -L1 bash -c 'dl "$0" "$1" '"$dir $root"
fi
have=$(ls "$dir" | grep -cE -- '-20261022(0[5-9]|1[0-9]|2[0-3])\.csv\.gz$')
want=$((n*19)); now=$(date -u +%s)
echo "$(date -u +%FT%TZ) probe BTC=$p1 ETH=$p2 файлов 22.10 05–23: $have из $want ($n контрактов)" >> "$log"
if [ "$have" -ge "$want" ]; then end="all: $have из $want"
elif [ "$now" -ge "$deadline" ]; then end="deadline: $have из $want (probe BTC=$p1 ETH=$p2)"
else exit 0; fi
echo "$(date -u +%FT%TZ) $end" > "$root/log/tk381.done"
systemctl stop "$TIMER" 2>/dev/null || true
