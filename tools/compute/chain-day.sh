#!/usr/bin/env bash
# Дождаться переноса вчерашних суток с коллектора (маркер — verify-logs/<день>.log приезжает последним)
# и запустить ночной скрипт сразу, не дожидаясь таймера 02:00Z (владелец 20.09: «19 число докачай, допрогони»).
set -u
cd /opt/alpha-compute || exit 1
DAY=${1:?день YYYY-MM-DD}
LOG=study/chain-day-$DAY.log
echo "== $(date -u +%FT%TZ) жду перенос $DAY (verify-logs/$DAY.log)" >> "$LOG"
for i in $(seq 1 120); do
  [ -f "verify-logs/$DAY.log" ] && break
  sleep 60
done
if [ ! -f "verify-logs/$DAY.log" ]; then echo "== $(date -u +%FT%TZ) переноса нет за 2 ч — стоп" >> "$LOG"; exit 1; fi
sleep 30
echo "== $(date -u +%FT%TZ) перенос есть: файлов $DAY в root $(ls root/*$DAY*.binlog* 2>/dev/null | wc -l), маркеров ok $(grep -l "^ok" root/verify-*.status 2>/dev/null | wc -l); запускаю ночной скрипт" >> "$LOG"
/opt/alpha-compute/bin/nightly-grid.sh
rc=$?
echo "== $(date -u +%FT%TZ) ночной скрипт кончился, код $rc" >> "$LOG"
exit $rc
