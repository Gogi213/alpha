#!/usr/bin/env bash
# TK-015: место на деке под янв–июн (CEO 05:30, Исследователь 05:32) — D20 месяца уезжает с деки на ящик, когда месяц
# прочитан (~/alpha/tmp-p07/h1-read/READY-<мес> или GATE-STOP-<мес>). rsync --remove-source-files сверяет и удаляет
# источник (и то, что на ящике уже лежит тем же, — сутки VPS); в каталоге суток остаются .done и .offloaded,
# чтобы циклы забора/сетки не взяли сутки заново. Копия: ящик alpha/derived/tk015/<эп>/D20/<сутки>/ (.box-done).
#   tk015-offload.sh  — кругами раз в 15 мин, до 36 ч или пока все шесть месяцев не сняты
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
R="$HOME/alpha/tmp-p07/h1-read"
say() { echo "== $(date -u +%FT%TZ) $*"; }
end=$(( $(date +%s) + 36 * 3600 ))
while :; do
  left=0
  for m in jan feb mar apr may jun; do
    E="e-$m"; H="$HOME/alpha/epochs/$E"
    [ -f "$H/.offloaded" ] && continue
    left=$((left + 1))
    [ -f "$R/READY-$m" ] || [ -f "$R/GATE-STOP-$m" ] || continue
    bad=0
    for d in "$H"/study/approaches/D20/*/; do
      day=$(basename "$d"); [ -f "$d/.offloaded" ] && continue
      [ -f "$d/.linked" ] && { touch "$d/.offloaded"; continue; }  # ссылки на ящик: там уже всё, заливать нечего
      n=$(ls "$d"approaches-*.csv 2>/dev/null | wc -l)
      if rsync -a --mkpath --remove-source-files --exclude=.done --exclude=.offloaded -e "$SSHC" \
           "$d" "$SBH:alpha/derived/tk015/$E/D20/$day/" \
         && touch "$d/.box-done" && rsync -a --remove-source-files -e "$SSHC" "$d/.box-done" "$SBH:alpha/derived/tk015/$E/D20/$day/"; then
        touch "$d/.offloaded"; say "$E $day: на ящик, файлов подходов $n"
      else
        bad=$((bad + 1)); say "$E $day: заливка упала"
      fi
    done
    [ "$bad" -eq 0 ] && touch "$H/.offloaded" && say "$E снят с деки, свободно $(df -h --output=avail "$H" | tail -1)"
  done
  [ "$left" -eq 0 ] && { say "все месяцы сняты"; exit 0; }
  [ "$(date +%s)" -ge "$end" ] && { say "36 ч вышли"; exit 1; }
  sleep 900
done
