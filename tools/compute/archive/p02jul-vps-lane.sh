#!/usr/bin/env bash
# TK-018 (В-151): полоса VPS для блока A П-02 на июле — те же сутки-задания `p02-jul-blockA-day.sh`, что на деке
# (тот же бинарник alpha-3a9fe23, md5 c937f542…; дом /home/deck/alpha — раскладка как на деке, TK-018 Инженер 12:06).
# Ждёт конца полос сетки jall на VPS, затем сутки по очереди; результат суток — на ящик
# /mnt/sb/alpha/derived/p02jul/<проход>/<сутки>.* (+ <сутки>.done), дека забирает `p02jul-box-recv.sh`.
#   systemd-run --unit=alpha-p02jul-laneA --setenv=HOME=/home/deck bash p02jul-vps-lane.sh 2026-07-31 2026-07-29 …
set -uo pipefail
H=/home/deck/alpha
BOX=/mnt/sb/alpha/derived/p02jul
while systemctl is-active -q alpha-jall-lane3 alpha-jall-lane4 alpha-jall-lane5; do sleep 60; done
for day in "$@"; do
  if HOME=/home/deck bash "$H/tmp-p02jul/p02-jul-blockA-day.sh" "$day" > "$H/tmp-p02jul/vps-$day.log" 2>&1; then
    for p in touches flow g33; do
      mkdir -p "$BOX/$p"
      cp "$H/epochs/e-jul/study/p02jul/$p/$day".* "$BOX/$p/"
    done
    cp "$H/tmp-p02jul/vps-$day.log" "$BOX/"
    touch "$BOX/$day.done"
  else
    echo "rc=$? $(date -u +%FT%TZ)" > "$BOX/$day.bad"
  fi
done
