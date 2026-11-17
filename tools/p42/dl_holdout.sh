#!/usr/bin/env bash
# П-42 §13.17: отложенные сутки (14.11 и позже) — только скачать и заморозить, в счёте TK-588 не читаются.
# aggTrades и минутки 1m Binance USD-M с data.binance.vision для 566 монет П-42, zip как опубликован + sha256 по .CHECKSUM.
# Запуск на srv: systemd-run --unit=p42-holdout-dl -p Nice=19 -p IOSchedulingClass=idle bash dl_holdout.sh 2026-11-14 2026-11-16
set -uo pipefail
D0="${1:?первый день}"; D1="${2:?последний день}"
OUT=/data/p42-holdout; mkdir -p "$OUT/log"; L="$OUT/log"
: > "$L/tasks.txt"
d="$D0"
while [ "$(date -u -d "$d" +%s)" -le "$(date -u -d "$D1" +%s)" ]; do
  tr ',' '\n' < /data/p42-t566/symbols.txt | sed "s/\$/ $d/" >> "$L/tasks.txt"
  d=$(date -u -d "$d +1 day" +%F)
done
one() { s=$1; d=$2; base=https://data.binance.vision/data/futures/um/daily
  for kind in aggTrades klines; do
    if [ "$kind" = aggTrades ]; then path="aggTrades/$s/$s-aggTrades-$d.zip"; dst="$OUT/aggtrades/$s"; else path="klines/$s/1m/$s-1m-$d.zip"; dst="$OUT/klines/$s"; fi
    f="$dst/$(basename "$path")"; [ -s "$f" ] && continue; mkdir -p "$dst"
    c=$(curl -s -m 300 --retry 3 -o "$f.part" -w '%{http_code}' "$base/$path")
    if [ "$c" != 200 ]; then rm -f "$f.part"; printf '%s\t%s\t%s\t%s\n' "$kind" "$s" "$d" "$c" >> "$L/miss.tsv"; continue; fi
    want=$(curl -s -m 60 --retry 3 "$base/$path.CHECKSUM" | awk '{print $1}')
    got=$(sha256sum "$f.part" | awk '{print $1}')
    if [ -n "$want" ] && [ "$want" = "$got" ]; then mv "$f.part" "$f"; echo "$want  $(basename "$f")" > "$f.sha256"
    else rm -f "$f.part"; printf '%s\t%s\t%s\tsha\n' "$kind" "$s" "$d" >> "$L/miss.tsv"; fi
  done; }
export -f one; export OUT L
echo "$(date -u +%T) старт: $(wc -l < "$L/tasks.txt") задач" | tee -a "$L/run.log"
xargs -a "$L/tasks.txt" -P 8 -L 1 bash -c 'one "$@"' _
echo "$(date -u +%T) файлов $(find "$OUT" -name '*.zip' | wc -l), не-200/sha: $(wc -l < "$L/miss.tsv" 2>/dev/null || echo 0), размер $(du -sh "$OUT" | cut -f1)" | tee -a "$L/run.log"
touch "$L/done"
