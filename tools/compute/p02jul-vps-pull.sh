#!/usr/bin/env bash
# TK-018: дека забирает сутки блока A П-02, посчитанные полосой VPS `p02jul-vps-lane2.sh` (ящик на VPS ro), из
# /opt/alpha-compute/p02jul-out ключом rrsync -ro (как deck-bulk-pull.sh) в epochs/e-jul/study/p02jul/<проход>/ и
# ставит метку суток .done — сторож `p02-jul-blockA-watch.sh` видит все 31. Сутки с .bad на VPS — тревога в лог, выход.
#   systemd-run --user --unit=alpha-p02jul-vpspull bash ~/alpha/tmp-p02jul/p02jul-vps-pull.sh 2026-07-21 …
set -uo pipefail
SSH="ssh -i $HOME/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=30"
SRC=root@13.140.29.171:p02jul-out
J="$HOME/alpha/epochs/e-jul/study/p02jul"
IN="$HOME/alpha/tmp-p02jul/vps-out"
mkdir -p "$IN" "$HOME/alpha/tmp-p02jul/logs"
left=("$@")
while [ ${#left[@]} -gt 0 ]; do
  rsync -a -e "$SSH" "$SRC/" "$IN/" || true
  rest=()
  for day in "${left[@]}"; do
    if [ -f "$IN/$day.bad" ]; then
      echo "$(date -u +%FT%TZ) VPS: $day упал ($(cat "$IN/$day.bad"))"; exit 1
    elif [ -f "$IN/$day.done" ] && [ ! -f "$J/$day.done" ]; then
      for p in touches flow g33; do mkdir -p "$J/$p"; cp "$IN/$p/$day".* "$J/$p/"; done
      cp "$IN/vps-$day.log" "$HOME/alpha/tmp-p02jul/logs/" 2>/dev/null
      touch "$J/$day.done"; echo "$(date -u +%FT%TZ) $day принят"
    elif [ ! -f "$J/$day.done" ]; then
      rest+=("$day")
    fi
  done
  left=("${rest[@]}")
  [ ${#left[@]} -gt 0 ] && sleep 120
done
exit 0
