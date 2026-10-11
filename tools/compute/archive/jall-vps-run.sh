#!/usr/bin/env bash
# TK-018: полоса счёта сеток июля на VPS (дом = путь деки). Аргументы — сутки по порядку (или vgate).
# Сутки ждут $H/vps-ready/<сутки> (D20 едет с деки через ящик, alpha-jall-pull). Итог суток — $H/vps-status/<сутки>.rc.
H=/home/deck/alpha/epochs/e-jul
CD=/home/deck/alpha/tmp-p07/cells-by-day
cd "$H" || exit 1
mkdir -p "$H/vps-status"
for d in "$@"; do
  if [ "$d" = vgate ]; then sh=$CD/vgate-jul-2026-07-31.sh; rd=2026-07-31; else sh=$CD/jall-jul-$d.sh; rd=$d; fi
  until [ -e "$H/vps-ready/$rd" ]; do sleep 30; done
  [ -e "$H/vps-status/$d.rc" ] && continue
  s=$(date +%s)
  bash "$sh" > "$CD/vps-$d.log" 2>&1
  echo "rc=$? sec=$(( $(date +%s) - s ))" > "$H/vps-status/$d.rc"
done
