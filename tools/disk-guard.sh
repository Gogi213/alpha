#!/usr/bin/env bash
# Сторож диска Steam Deck (CEO 27.09, после заполнения /home до 0 байт в 00:00 UTC — чужой кэш уронил счёт).
#   disk-guard.sh            — раз в минуту (alpha-disk-guard.timer): свободно < LOW_GB → заморозить все
#                              идущие счётные юниты alpha-* (active и activating — oneshot идёт как activating; `systemctl --user freeze`), кроме забора, ящика и
#                              самого сторожа; метка ~/alpha/sync/DISK-FULL (время, свободно, список). Свободно
#                              > HIGH_GB → разморозить по метке, метку снять. Лог — ~/alpha/sync/disk-guard.log.
#   disk-guard.sh prepull    — ExecStartPre забора: нужно NEED_GB под новые сутки; не хватает — сначала ночной
#                              перенос на ящик (`sb-night.sh` снимает сверенный deep/); всё равно не хватает —
#                              забор сегодня пропускается (коллектор держит ~3 суток, догонит), метка DISK-FULL.
set -uo pipefail
LOW_GB="${LOW_GB:-10}"; HIGH_GB="${HIGH_GB:-15}"; NEED_GB="${NEED_GB:-12}"
A="$HOME/alpha"; MARK="$A/sync/DISK-FULL"; LOG="$A/sync/disk-guard.log"
free_gb() { df --output=avail -BG "$A" | tail -1 | tr -dc 0-9; }
say() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
# забор, ящик, сторож — не замораживаются; TK-016: и любые alpha-*-pull-*/-tobox/-offload (заборы и
# перенос на ящик под задачу, напр. alpha-tk015-pull-*) — заморозка посреди rsync держит место занятым
SPARE='^alpha-(pull|sb-|disk-guard)|^alpha-.*-(pull|tobox|offload)([-.]|$)'

if [ "${1:-}" = prepull ]; then
  f=$(free_gb)
  [ "$f" -ge "$NEED_GB" ] && exit 0
  say "перед забором свободно ${f} ГБ < ${NEED_GB} — ночной перенос на ящик"
  bash "$A/bin/sb-night.sh"
  f=$(free_gb)
  [ "$f" -ge "$NEED_GB" ] && { say "после переноса свободно ${f} ГБ — забор идёт"; exit 0; }
  say "после переноса свободно ${f} ГБ < ${NEED_GB} — забор сегодня пропущен"
  echo "$(date -u +%FT%TZ) забор пропущен: свободно ${f} ГБ < ${NEED_GB}" >> "$MARK"
  exit 1
fi

f=$(free_gb)
if [ "$f" -lt "$LOW_GB" ] && ! grep -q '^frozen ' "$MARK" 2>/dev/null; then
  units=$(systemctl --user list-units 'alpha-*' --type=service --state=active,activating --no-legend --plain \
    | awk '{print $1}' | grep -Ev "$SPARE")
  { echo "$(date -u +%FT%TZ) свободно ${f} ГБ < ${LOW_GB} — счёт заморожен до > ${HIGH_GB} ГБ"
    for u in $units; do systemctl --user freeze "$u" && echo "frozen $u"; done; } >> "$MARK"
  say "заморожено: $(grep -c '^frozen ' "$MARK") юнит(ов), свободно ${f} ГБ"
elif [ "$f" -gt "$HIGH_GB" ] && grep -q '^frozen ' "$MARK" 2>/dev/null; then
  for u in $(awk '$1 == "frozen" {print $2}' "$MARK"); do systemctl --user thaw "$u" 2>/dev/null; done
  say "разморожено, свободно ${f} ГБ"
  mv "$MARK" "$A/sync/DISK-FULL.$(date -u +%Y%m%dT%H%M%S).done"
fi
