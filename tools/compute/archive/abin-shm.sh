#!/bin/bash
# abin-shm.sh up <каталог abin> <тег> <сутки…> | down <тег> — копия .abin заданных суток в /dev/shm/abin-<тег>, смонтированная только на чтение
# (промах кэша не пишет в ОЗУ). Путь для ALPHA_APPROACH_BIN_DIR печатается; объём (need_mb) — в --mem задания alsched. Нет суток в кэше — ABORT (ALLOW_MISS=1 снимает).
set -eu
case "${1:?up|down}" in
up)
  SRC=${2:?каталог abin}; TAG=${3:?тег}; shift 3; D=/dev/shm/abin-$TAG; RES=${SHM_RESERVE_MB:-2048}
  need=0; miss=0; days=0
  for d in "$@"; do p=$SRC/study/approaches/D20/$d
    if [ -d "$p" ]; then need=$((need + $(du -sLm "$p" | cut -f1) + 1)); days=$((days+1)); else miss=$((miss+1)); fi
  done
  [ "$days" -gt 0 ] || { echo "ABORT: ни одних суток не найдено в $SRC" >&2; exit 3; }
  [ "$miss" -eq 0 ] || [ -n "${ALLOW_MISS:-}" ] || { echo "ABORT: нет abin для $miss суток из $#" >&2; exit 3; }
  avail=$(df -m --output=avail /dev/shm | tail -1)
  [ $((need+RES)) -le "$avail" ] || { echo "ABORT: шм: нужно $need МБ + резерв $RES > свободно $avail" >&2; exit 4; }
  mountpoint -q "$D" && umount "$D"; rm -rf "$D" "$D.rw"; mkdir -p "$D.rw/study/approaches/D20" "$D"
  for d in "$@"; do p=$SRC/study/approaches/D20/$d; [ -d "$p" ] && cp -aL "$p" "$D.rw/study/approaches/D20/$d"; done
  mount --bind "$D.rw" "$D" && mount -o remount,ro,bind "$D"
  echo "SHM_ABIN_DIR=$D need_mb=$need days=$days miss=$miss files=$(find "$D" -type f | wc -l)"
  ;;
down)
  D=/dev/shm/abin-${2:?тег}; mountpoint -q "$D" && umount "$D"; rm -rf "$D" "$D.rw"
  ;;
*) echo "up|down"; exit 2 ;;
esac
