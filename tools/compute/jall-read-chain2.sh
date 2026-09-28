#!/usr/bin/env bash
# TK-018: чтение jall с перезапуском после потери задачи пула. mp.Pool не переживает смерть воркера (earlyoom убил
# один в 14:52:24 → imap ждал бы вечно): сторож сравнивает набор воркеров; пул подменил воркер → задача потеряна →
# убить читатель и запустить снова с --resume (готовые строки — с диска, <имя>/ps-closes.json). Не больше 6 попыток.
# Дальше — как jall-read-chain.sh: выгрузка → ждать j9-read → маркер jall-read.finished.
T=/home/deck/alpha/tmp-p07; J=${JOBS:-4}
cd $T || exit 1
mkdir -p /home/deck/alpha/tmp-dash
for try in 1 2 3 4 5 6; do
  setsid python3 p07-all-jul-read.py --out $T/jall-read/jall-read.json --jobs $J --resume >> $T/jall-read.log 2>&1 &
  rp=$!; k0=""; rc=""
  while kill -0 $rp 2>/dev/null; do
    sleep 20
    k=$(pgrep -P $rp | sort | tr '\n' ' ')
    [ -z "$k0" ] && [ "$(echo $k | wc -w)" -ge "$J" ] && k0=$k
    if [ -n "$k0" ] && [ -n "$k" ] && [ "$k" != "$k0" ] && [ "$(echo $k | wc -w)" -ge "$J" ]; then
      echo "== $(date -u +%FT%TZ) сторож: воркеры сменились ($k0 → $k) — задача пула потеряна, перезапуск" >> $T/jall-read.log
      kill -KILL -- -$rp; rc=lost; break
    fi
  done
  [ "$rc" = lost ] && { sleep 5; continue; }
  wait $rp; rc=$?; break
done
[ "$rc" = 0 ] && python3 dash-jall-dump.py > /home/deck/alpha/tmp-dash/jall.json 2>> $T/jall-read.log
rc2=$?
until [ -e $T/j9-read.finished ]; do sleep 60; done
echo "read rc=$rc dump rc=$rc2 $(date -u +%FT%TZ)" > $T/jall-read.finished
