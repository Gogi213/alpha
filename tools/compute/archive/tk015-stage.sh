#!/usr/bin/env bash
# TK-015 (CEO 11:25: «месяц на деку → сетка → снять D20»): сетка p07-h1 по ссылкам на ящик идёт ~×2 дольше (одна
# проба 04:49Z — 1580 с против 600–900 с по локальным файлам), а целый месяц файлами ест 5–8 ГБ из 10 свободных над
# планкой 15 ГБ. Поэтому — скользящее окно: локально лежат D20 только тех суток (и следующих за ними — перенос), чьи
# задания p07-h1 идут или стоят первыми в очереди; остальные — ссылками на ящик (.linked). Подмена каталога — двумя mv
# через study/approaches/tobox-tmp/ (вне D20/*), как в tk015-tobox-link.sh; открытые файлы дочитываются из старого.
#   tk015-stage.sh [K=12]   — кругами раз в 3 мин; конец — 60 мин без заданий p07-h1 или 36 ч; в конце всё ссылками
set -uo pipefail
K="${1:-12}"; Q="$HOME/alpha/queue"; MINFREE=15
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
say() { echo "== $(date -u +%FT%TZ) $*"; }
free_gb() { df --output=avail -BG "$HOME/alpha" | tail -1 | tr -dc 0-9; }

# задания p07-h1: идущие, затем очередь в её порядке → строки «<эпоха> <сутки>»
jobs_order() {
  for f in $(ls "$Q"/running/*p07-h1*.job 2>/dev/null) $(ls "$Q"/pending/*p07-h1*.job 2>/dev/null | sort); do
    e=$(grep -m1 '^JOB_HOME=' "$f" | sed -E 's#.*/epochs/(e-[a-z]{3}).*#\1#')
    d=$(grep -m1 '^JOB_CMD=' "$f" | grep -oE 'root-20[0-9]{2}-[0-9]{2}-[0-9]{2}' | head -1 | cut -c6-)
    [ -n "$e" ] && [ -n "$d" ] && echo "$e $d"
  done
}

swap_in() {  # $1 эпоха, $2 сутки: ссылки → локальные файлы
  local E=$1 day=$2 D T
  D="$HOME/alpha/epochs/$E/study/approaches/D20/$day"; T="$HOME/alpha/epochs/$E/study/approaches/tobox-tmp"
  [ -f "$D/.linked" ] && [ ! -f "$D/.local" ] || return 0
  mkdir -p "$T"; rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.local"
  # rsync по ssh, не через sshfs: 456 МБ за 64 с (~7 МБ/с) против ~1 МБ/с по ~/sb под нагрузкой деки
  rsync -a -z --exclude='.*' -e "$SSHC" "$SBH:alpha/derived/tk015/$E/D20/$day/" "$T/$day.local/" \
    || { say "$E $day: копия с ящика упала"; return 1; }
  cp -p "$D"/.done "$D"/.linked "$D"/.offloaded "$T/$day.local/" 2>/dev/null; touch "$T/$day.local/.local"
  mv "$D" "$T/$day.links" && mv "$T/$day.local" "$D" || { say "$E $day: подмена не вышла"; return 1; }
  rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.links"
  say "$E $day: локально ($(ls "$D"/approaches-*.csv | wc -l) подходов), свободно $(free_gb) ГБ"
}

swap_out() {  # $1 эпоха, $2 сутки: локальные файлы → ссылки на ящик
  local E=$1 day=$2 D T f
  D="$HOME/alpha/epochs/$E/study/approaches/D20/$day"; T="$HOME/alpha/epochs/$E/study/approaches/tobox-tmp"
  [ -f "$D/.local" ] || return 0
  mkdir -p "$T"; rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.links"; mkdir -p "$T/$day.links"
  for f in "$HOME/sb/derived/tk015/$E/D20/$day"/*; do
    [ "$(basename "$f")" = symbols.txt ] || ln -s "$f" "$T/$day.links/$(basename "$f")"
  done
  cp -p "$D"/.done "$D"/.linked "$D"/.offloaded "$T/$day.links/" 2>/dev/null
  mv "$D" "$T/$day.local" && mv "$T/$day.links" "$D" || { say "$E $day: обратная подмена не вышла"; return 1; }
  rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.local"
  say "$E $day: снова ссылками"
}

end=$(( $(date +%s) + 36 * 3600 )); idle_since=$(date +%s)
while :; do
  order=$(jobs_order)
  if [ -n "$order" ]; then idle_since=$(date +%s); fi
  # окно: первые K заданий + следующие сутки той же эпохи (перенос позиции через полночь)
  want=$(echo "$order" | head -n "$K" | while read -r e d; do
    [ -n "$e" ] || continue; echo "$e $d"; echo "$e $(date -u -d "$d +1 day" +%F)"; done | sort -u)
  # снять локальные вне окна
  for D in "$HOME"/alpha/epochs/e-{jan,feb,mar,apr,may,jun}/study/approaches/D20/*/.local; do
    [ -f "$D" ] || continue
    dd=$(dirname "$D"); day=$(basename "$dd"); e=$(echo "$dd" | sed -E 's#.*/epochs/(e-[a-z]{3})/.*#\1#')
    echo "$want" | grep -qx "$e $day" || swap_out "$e" "$day"
  done
  # положить окно локально, пока свободно ≥ MINFREE + 1
  echo "$want" | while read -r e d; do
    [ -n "$e" ] && [ -d "$HOME/alpha/epochs/$e/study/approaches/D20/$d" ] || continue
    [ "$(free_gb)" -ge $((MINFREE + 1)) ] || { say "свободно $(free_gb) ГБ — окно не расширяю"; break; }
    swap_in "$e" "$d"
  done
  now=$(date +%s)
  if [ $((now - idle_since)) -ge 3600 ] || [ "$now" -ge "$end" ]; then
    for D in "$HOME"/alpha/epochs/e-{jan,feb,mar,apr,may,jun}/study/approaches/D20/*/.local; do
      [ -f "$D" ] || continue
      dd=$(dirname "$D"); swap_out "$(echo "$dd" | sed -E 's#.*/epochs/(e-[a-z]{3})/.*#\1#')" "$(basename "$dd")"
    done
    say "конец: заданий p07-h1 нет 60 мин или 36 ч вышли"; exit 0
  fi
  sleep 180
done
