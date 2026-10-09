#!/usr/bin/env bash
# TK-018: дом июля на VPS по тому же абсолютному пути, что на деке (/home/deck/alpha/epochs/e-jul) — сценарии суток
# tmp-p07/cells-by-day/jall-jul-*.sh идут без правки путей. Бинлоги — ссылками на ящик (/home/deck/sb → /mnt/sb/alpha),
# бинарник — /opt/alpha-compute/bin (alpha-e74f200-v3, md5 6b1974f4… = дека). D20 — копией с ящика (выкладывает
# jall-vps-push.sh на деке), сутки с 31-го; готовность суток — /home/deck/alpha/epochs/e-jul/vps-ready/<сутки>.
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
H=/home/deck/alpha/epochs/e-jul; B=/mnt/sb/alpha/derived/jall; W=/opt/alpha-compute/jall
say() { echo "== $(date -u +%FT%TZ) $*"; }
mountpoint -q /mnt/sb || { say "ящик не смонтирован"; exit 1; }
mkdir -p /home/deck/alpha/tmp-p07/cells-by-day "$H/vps-ready" "$H/study/approaches/D20" "$W"
[ -e /home/deck/sb ] || ln -s /mnt/sb/alpha /home/deck/sb
[ -e /home/deck/alpha/bin ] || ln -s /opt/alpha-compute/bin /home/deck/alpha/bin
until [ -f "$B/e-jul-home.tar.box-done" ]; do sleep 30; done
if [ ! -f "$H/.home-done" ]; then
  rsync -a -e "$SSHC" "$SBH:alpha/derived/jall/e-jul-home.tar" "$W/home.tar" \
    && tar xf "$W/home.tar" -C "$H" && touch "$H/.home-done" || { say "дом не распакован"; exit 1; }
  say "дом распакован"
fi
for dd in $(seq 31 -1 1); do
  day=2026-07-$(printf %02d "$dd")
  [ -f "$H/vps-ready/$day" ] && continue
  until [ -f "$B/D20/$day/.box-done" ]; do sleep 30; done
  free=$(df --output=avail -BG /opt/alpha-compute | tail -1 | tr -dc 0-9)
  [ "$free" -ge 6 ] || { say "$day: свободно ${free} ГБ < 6 — стоп"; exit 3; }
  if rsync -a --exclude=.box-done -e "$SSHC" "$SBH:alpha/derived/jall/D20/$day/" "$H/study/approaches/D20/$day/"; then
    touch "$H/vps-ready/$day"; say "$day: $(ls "$H/study/approaches/D20/$day" | wc -l) файлов"
  else
    say "$day: забор упал"
  fi
done
say конец
