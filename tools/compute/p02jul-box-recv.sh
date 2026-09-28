#!/usr/bin/env bash
# TK-018: дека забирает сутки блока A П-02, посчитанные на VPS (`p02jul-vps-lane.sh`), с ящика (~/sb = /mnt/sb/alpha)
# в epochs/e-jul/study/p02jul/<проход>/ и ставит метку суток .done — сторож `p02-jul-blockA-watch.sh` видит все 31.
#   systemd-run --user --unit=alpha-p02jul-recv bash ~/alpha/tmp-p02jul/p02jul-box-recv.sh 2026-07-24 … 2026-07-31
set -uo pipefail
BOX="$HOME/sb/derived/p02jul"
J="$HOME/alpha/epochs/e-jul/study/p02jul"
left=("$@")
while [ ${#left[@]} -gt 0 ]; do
  rest=()
  for day in "${left[@]}"; do
    if [ -f "$BOX/$day.done" ] && [ ! -f "$J/$day.done" ]; then
      for p in touches flow g33; do mkdir -p "$J/$p"; cp "$BOX/$p/$day".* "$J/$p/"; done
      cp "$BOX/vps-$day.log" "$HOME/alpha/tmp-p02jul/logs/" 2>/dev/null
      touch "$J/$day.done"
    elif [ ! -f "$J/$day.done" ]; then
      rest+=("$day")
    fi
  done
  left=("${rest[@]}")
  [ ${#left[@]} -gt 0 ] && sleep 120
done
