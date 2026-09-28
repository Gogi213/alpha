#!/usr/bin/env bash
# TK-018: итоги сетки июля с VPS обратно на деку через ящик (ключей VPS↔дека нет).
#   на VPS:  jall-vps-back.sh send  — сутки с $H/vps-status/<d>.rc = rc=0 → alpha/derived/jall/out/<d>.tar (+ .box-done)
#   на деке: jall-vps-back.sh recv  — каждый out/<d>.tar с .box-done → ~/alpha/epochs/e-jul/vps-in/<d>/ (НЕ в b5:
#            07-31 и vgate дека считает сама для сверки; перенос в b5 — после сверки, Исследователь)
# В tar: b5/*/<сутки>/ (у vgate — только b5/*vpsgate*/2026-07-31/) и логи tmp-p07/cells-by-day/*<d>*.
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
H=/home/deck/alpha/epochs/e-jul; CD=/home/deck/alpha/tmp-p07/cells-by-day
say() { echo "== $(date -u +%FT%TZ) $*"; }
case "${1:?send|recv}" in
send)
  SSHC="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
  W=/opt/alpha-compute/jall/out; mkdir -p "$W"; cd "$H" || exit 1
  while :; do
    for rc in vps-status/*.rc; do
      [ -e "$rc" ] || continue
      d=$(basename "$rc" .rc); [ -e "$W/$d.sent" ] && continue
      grep -q '^rc=0 ' "$rc" || { [ -e "$W/$d.bad" ] || { say "$d: $(cat "$rc") — не шлю"; : > "$W/$d.bad"; }; continue; }
      if [ "$d" = vgate ]; then dirs=$(ls -d b5/*vpsgate*/2026-07-31 2>/dev/null); lg=$(ls "$CD"/vps-vgate.log 2>/dev/null)
      else dirs=$(ls -d b5/*/"$d" 2>/dev/null | grep -v vpsgate); lg=$(ls "$CD"/*"$d"* 2>/dev/null); fi
      [ -n "$dirs" ] || { say "$d: rc=0, но в b5 нет каталогов суток"; : > "$W/$d.sent"; continue; }
      tar cf "$W/$d.tar" $dirs $lg \
        && rsync -a --mkpath -e "$SSHC" "$W/$d.tar" "$SBH:alpha/derived/jall/out/$d.tar" \
        && : > "$W/$d.box-done" && rsync -a -e "$SSHC" "$W/$d.box-done" "$SBH:alpha/derived/jall/out/$d.tar.box-done" \
        && : > "$W/$d.sent" && say "$d: $(echo "$dirs" | wc -l) каталогов, $(du -h "$W/$d.tar" | cut -f1)" \
        || say "$d: отправка упала — повтор"
    done
    sleep 60
  done ;;
recv)
  B=$HOME/sb/derived/jall/out; I=$HOME/alpha/epochs/e-jul/vps-in; mkdir -p "$I"
  while :; do
    for m in "$B"/*.tar.box-done; do
      [ -e "$m" ] || continue
      d=$(basename "$m" .tar.box-done); [ -e "$I/$d.done" ] && continue
      mkdir -p "$I/$d" && tar xf "$B/$d.tar" -C "$I/$d" && : > "$I/$d.done" && say "$d: принято в vps-in/$d" \
        || say "$d: распаковка упала — повтор"
    done
    sleep 60
  done ;;
esac
