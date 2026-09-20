#!/usr/bin/env bash
# Дождаться переноса вчерашних суток с коллектора (маркер — verify-logs/<день>.log приезжает последним)
# и запустить ночной скрипт сразу, не дожидаясь таймера 02:00Z (владелец 20.09: «19 число докачай, допрогони»).
set -u
cd /opt/alpha-compute || exit 1
DAY=${1:?день YYYY-MM-DD}
LOG=study/chain-day-$DAY.log
echo "== $(date -u +%FT%TZ) жду перенос $DAY (файлов дня = маркеров verify)" >> "$LOG"
# Маркер переноса: число суточных файлов дня в root равно числу монет с маркером verify (перенос
# идёт rsync-ом по имени дня; verify-logs/ он не кладёт — ошибка первой версии 20.09, ждала зря).
want=$(ls root/verify-*.status 2>/dev/null | wc -l)
for i in $(seq 1 120); do
  have=$(ls root/*"$DAY"*.binlog* 2>/dev/null | wc -l)
  [ "$want" -gt 0 ] && [ "$have" -ge "$want" ] && break
  sleep 60
done
have=$(ls root/*"$DAY"*.binlog* 2>/dev/null | wc -l)
if [ "$want" -eq 0 ] || [ "$have" -lt "$want" ]; then echo "== $(date -u +%FT%TZ) переноса нет за 2 ч ($have из $want файлов) — стоп" >> "$LOG"; exit 1; fi
# rsync мог ещё дописывать последний файл — минута тишины.
sleep 60
echo "== $(date -u +%FT%TZ) перенос есть: файлов $DAY в root $(ls root/*$DAY*.binlog* 2>/dev/null | wc -l), маркеров ok $(grep -l "^ok" root/verify-*.status 2>/dev/null | wc -l); запускаю ночной скрипт" >> "$LOG"
/opt/alpha-compute/bin/nightly-grid.sh
rc=$?
echo "== $(date -u +%FT%TZ) ночной скрипт кончился, код $rc" >> "$LOG"
exit $rc
