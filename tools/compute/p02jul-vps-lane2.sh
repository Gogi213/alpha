#!/usr/bin/env bash
# TK-018 (В-151): полоса VPS блока A П-02, вариант 2 (15:40 GMT+4). Ящик на VPS смонтирован только на чтение
# (/mnt/sb ro — полосы laneA/laneB 07-24…31 посчитали, но упали на выгрузке), поэтому итог суток кладётся в
# /opt/alpha-compute/p02jul-out/<проход>/<сутки>.* (+ <сутки>.done) — дека тянет его своим ключом rrsync -ro
# (`p02jul-vps-pull.sh`). Без ожидания полос jall; nice 19 — ниже чтения jall.
#   systemd-run --unit=alpha-p02jul-laneC --setenv=HOME=/home/deck nice -n 19 bash p02jul-vps-lane2.sh 2026-07-21 …
set -uo pipefail
H=/home/deck/alpha
OUT=/opt/alpha-compute/p02jul-out
for day in "$@"; do
  if HOME=/home/deck bash "$H/tmp-p02jul/p02-jul-blockA-day.sh" "$day" > "$H/tmp-p02jul/vps-$day.log" 2>&1; then
    for p in touches flow g33; do
      mkdir -p "$OUT/$p"
      cp "$H/epochs/e-jul/study/p02jul/$p/$day".* "$OUT/$p/"
    done
    cp "$H/tmp-p02jul/vps-$day.log" "$OUT/"
    touch "$OUT/$day.done"
  else
    echo "rc=$? $(date -u +%FT%TZ)" > "$OUT/$day.bad"
  fi
done
