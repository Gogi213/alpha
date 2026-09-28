#!/usr/bin/env bash
# TK-015: кэш подходов D20 (бинарник T-28 16f0a80 = VPS bin/alpha-t28, md5 4a8038dc… как deck bin/alpha-16f0a80) для
# эпох янв–июн на VPS; бинлоги — с ящика /mnt/sb (sshfs ro, ~11 МБ/с против ~4–7 на деке). Сутки — как `day` у
# epoch-box-prep.sh (корень-день ссылками, K1 из verify-logs, touches D20), без режима: его считает дека после забора.
#   tk015-vps-d20.sh <эпоха> <сутки…>  → /opt/alpha-compute/tk015/<эпоха>/study/approaches/D20/<сутки>/ + .vps-done
set -uo pipefail
E="${1:?эпоха}"; shift
SB=/mnt/sb/alpha/epochs/$E/root; BIN=/opt/alpha-compute/bin/alpha-t28
mountpoint -q /mnt/sb || { echo "ящик не смонтирован"; exit 1; }
mkdir -p "/opt/alpha-compute/tk015/$E"; cd "/opt/alpha-compute/tk015/$E" || exit 1
for day in "$@"; do
  out="study/approaches/D20/$day"
  [ -f "$out/.vps-done" ] && { echo "$day уже готов"; continue; }
  rm -rf "/opt/alpha-compute/tk015/$E/study/root-$day" "/opt/alpha-compute/tk015/$E/study/approaches/D20/$day"
  dir="study/root-$day"; mkdir -p "$dir" "$out" "study/touches/$day"
  for f in "$SB"/*-"$day".binlog "$SB"/*-"$day"-p*.binlog "$SB/instruments.csv"; do
    [ -e "$f" ] && ln -s "$f" "$dir/"
  done
  echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$dir/session.json"
  grep -aE '^verify: [A-Z0-9]+ status=(ok|fail)' "$SB/verify-logs/$day.log" | while read -r _ sym st _; do
    echo "${st#status=}" > "$dir/verify-$sym.status"
  done
  grep -l '^ok$' "$dir"/verify-*.status | xargs -n1 basename | sed 's/^verify-//; s/\.status$//' > "study/touches/$day/symbols.txt"
  n=$(wc -l < "study/touches/$day/symbols.txt"); t0=$(date +%s)
  xargs -r -P "${JOBS:-4}" -I{} nice -n 15 bash -c \
    "$BIN lob touches --root '$dir' --symbol {} --h3-mode notional --h3-usd 10000 \
       --approach-bps 20 --out '$out/touches-{}.csv' >'$out/{}.log' 2>&1 || echo {} >> '$out/failed.txt'" \
    < "study/touches/$day/symbols.txt"
  files=$(ls "$out"/approaches-*.csv 2>/dev/null | wc -l)
  [ "$files" -ge "$n" ] && [ ! -f "$out/failed.txt" ] && touch "$out/.vps-done"
  echo "== $(date -u +%FT%TZ) $E $day: ok-монет $n, файлов подходов $files, $(( $(date +%s) - t0 )) с"
done
