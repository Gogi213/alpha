#!/usr/bin/env bash
# TK-015: кэш подходов D20 (бинарник T-28 16f0a80 = VPS bin/alpha-t28, md5 4a8038dc… как deck bin/alpha-16f0a80) для
# эпох янв–июн на VPS; бинлоги — с ящика /mnt/sb (sshfs ro, ~11 МБ/с против ~4–7 на деке). Сутки — как `day` у
# epoch-box-prep.sh (корень-день ссылками, K1 из verify-logs, touches D20), без режима: его считает дека после забора.
#   tk015-vps-d20.sh <эпоха> <сутки…>  → /opt/alpha-compute/tk015/<эпоха>/study/approaches/D20/<сутки>/ + .vps-done
set -uo pipefail
E="${1:?эпоха}"; shift
SB=/mnt/sb/alpha/epochs/$E/root; BIN=/opt/alpha-compute/bin/alpha-t28
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
mountpoint -q /mnt/sb || { echo "ящик не смонтирован"; exit 1; }
mkdir -p "/opt/alpha-compute/tk015/$E"; cd "/opt/alpha-compute/tk015/$E" || exit 1
for day in "$@"; do
  out="study/approaches/D20/$day"
  [ -f "$out/.vps-done" ] && { echo "$day уже готов"; continue; }
  free=$(df --output=avail -BG /opt/alpha-compute | tail -1 | tr -dc 0-9)
  [ "$free" -ge 6 ] || { echo "== $E $day: на VPS свободно ${free} ГБ < 6 — стоп"; exit 3; }
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
  if [ "$files" -ge "$n" ] && [ ! -f "$out/failed.txt" ]; then
    # Места на VPS и деке на полугодие нет (D20 ~7 ГБ/мес): сутки уезжают на ящик (rsync сверяет и удаляет источник),
    # дека забирает их с ~/sb/derived/tk015 (tk015-deck-pull.sh); здесь остаётся только маркер .vps-done.
    cp "study/touches/$day/symbols.txt" "$out/symbols.txt"
    if rsync -a --mkpath --remove-source-files -e "$SSHC" "$out/" "$SBH:alpha/derived/tk015/$E/D20/$day/" \
      && touch "$out/.box-done" \
      && rsync -a --remove-source-files -e "$SSHC" "$out/.box-done" "$SBH:alpha/derived/tk015/$E/D20/$day/"; then
      touch "$out/.vps-done"  # маркер последним: на ящике .box-done = сутки целиком
    else
      echo "== $E $day: заливка на ящик упала"
    fi
  fi
  echo "== $(date -u +%FT%TZ) $E $day: ok-монет $n, файлов подходов $files, $(( $(date +%s) - t0 )) с"
done
