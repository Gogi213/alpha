#!/usr/bin/env bash
# TK-015: дека забирает с VPS (rrsync -ro /opt/alpha-compute) кэш D20 суток, посчитанных tk015-vps-d20.sh, и доделывает
# сутки как `day` у epoch-box-prep.sh: корень-день (ссылки через root/ эпохи, K1 из verify-logs), режим суток, .done.
# По .done суток досылает сетку цикл Исследователя (p07-h1-loop.sh).
#   tk015-deck-pull.sh <эпоха> <сутки…>   — ходит кругами раз в 5 мин, пока все сутки не .done (не дольше 12 ч)
set -uo pipefail
E="${1:?эпоха}"; shift
H="$HOME/alpha/epochs/$E"; VPS=root@13.140.29.171
cd "$H" || exit 1
say() { echo "== $(date -u +%FT%TZ) $E $*"; }
end=$(( $(date +%s) + 12 * 3600 ))
while :; do
  left=0
  for day in "$@"; do
    [ -f "study/approaches/D20/$day/.done" ] && continue
    left=$((left + 1))
    rsync --list-only "$VPS:tk015/$E/study/approaches/D20/$day/.vps-done" >/dev/null 2>&1 || continue  # ключ — только rrsync
    mkdir -p "study/approaches/D20/$day" "study/touches/$day"
    rsync -a --exclude=.vps-done "$VPS:tk015/$E/study/approaches/D20/$day/" "study/approaches/D20/$day/" \
      && rsync -a "$VPS:tk015/$E/study/touches/$day/symbols.txt" "study/touches/$day/symbols.txt" || { say "$day: rsync упал"; continue; }
    dir="study/root-$day"; mkdir -p "$dir"
    for f in root/*-"$day".binlog root/*-"$day"-*.binlog root/instruments.csv root/session.json; do
      [ -e "$f" ] && [ ! -e "$dir/$(basename "$f")" ] && ln -s "$PWD/$f" "$dir/$(basename "$f")"
    done
    grep -aE '^verify: [A-Z0-9]+ status=(ok|fail)' "root/verify-logs/$day.log" | while read -r _ sym st _; do
      echo "${st#status=}" > "$dir/verify-$sym.status"
    done
    n=$(wc -l < "study/touches/$day/symbols.txt")
    files=$(ls "study/approaches/D20/$day"/approaches-*.csv 2>/dev/null | wc -l)
    python3 bin/regime.py --day "$day" --touches study/approaches/D20 | tail -1
    if [ "$files" -ge "$n" ] && [ ! -f "study/approaches/D20/$day/failed.txt" ] && [ -f "study/regime/$day.csv" ]; then
      touch "study/approaches/D20/$day/.done"; left=$((left - 1))
    fi
    say "$day: ok-монет $n, файлов подходов $files (с VPS)"
  done
  [ "$left" -eq 0 ] && { say "все сутки готовы"; exit 0; }
  [ "$(date +%s)" -ge "$end" ] && { say "12 ч вышли, не готово $left"; exit 1; }
  sleep 300
done
