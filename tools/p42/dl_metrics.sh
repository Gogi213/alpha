#!/usr/bin/env bash
# П-42 §13.20 (TK-594): производные метрики Binance USD-M — `metrics` (суточные архивы, 5 мин) и `fundingRate` (месячные архивы; нужны для 07–08.2026,
# в /data/klines/binance/*/funding.csv фандинг только с 01.09) с data.binance.vision для монет П-42; zip как опубликован + sha256 по .CHECKSUM.
# Запуск на srv: systemd-run --unit=p42-metrics-dl -p Nice=19 -p IOSchedulingClass=idle --collect bash dl_metrics.sh 2026-07-18 2026-11-17 "2026-07 2026-08"
# KIND=premium — суточные 1m `premiumIndexKlines` (OUT=/data/p42-premium); LOGDIR — другой каталог журнала (параллельные запуски не трут друг другу tasks.txt/miss.tsv).
# Раскладка: $OUT/<SYM>/<SYM>-metrics-<день>.zip (premium: <SYM>-1m-<день>.zip), $OUT/funding/<SYM>/<SYM>-fundingRate-<месяц>.zip (+ .sha256); не-200/sha — $L/miss.tsv.
set -uo pipefail
D0="${1:?первый день}"; D1="${2:?последний день}"; MONTHS="${3:-}"
KIND="${KIND:-metrics}"; OUT="${OUT:-/data/p42-metrics}"; L="${LOGDIR:-$OUT/log}"; mkdir -p "$L"
: > "$L/tasks.txt"
for m in $MONTHS; do tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' | sed "s/\$/ fundingRate $m/" >> "$L/tasks.txt"; done
d="$D0"
while [ "$(date -u -d "$d" +%s)" -le "$(date -u -d "$D1" +%s)" ]; do
  tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' | sed "s/\$/ $KIND $d/" >> "$L/tasks.txt"
  d=$(date -u -d "$d +1 day" +%F)
done
one() { s=$1; kind=$2; d=$3; base=https://data.binance.vision/data/futures/um
  if [ "$kind" = metrics ]; then path="daily/metrics/$s/$s-metrics-$d.zip"; dst="$OUT/$s"
  elif [ "$kind" = premium ]; then path="daily/premiumIndexKlines/$s/1m/$s-1m-$d.zip"; dst="$OUT/$s"; else path="monthly/fundingRate/$s/$s-fundingRate-$d.zip"; dst="$OUT/funding/$s"; fi
  f="$dst/$(basename "$path")"; [ -s "$f" ] && return 0; mkdir -p "$dst"
  c=$(curl -s -m 120 --retry 3 -o "$f.part" -w '%{http_code}' "$base/$path")
  if [ "$c" != 200 ]; then rm -f "$f.part"; printf '%s\t%s\t%s\t%s\n' "$kind" "$s" "$d" "$c" >> "$L/miss.tsv"; return 0; fi
  want=$(curl -s -m 60 --retry 3 "$base/$path.CHECKSUM" | awk '{print $1}')
  got=$(sha256sum "$f.part" | awk '{print $1}')
  if [ -n "$want" ] && [ "$want" = "$got" ]; then mv "$f.part" "$f"; echo "$want  $(basename "$f")" > "$f.sha256"
  else rm -f "$f.part"; printf '%s\t%s\t%s\tsha\n' "$kind" "$s" "$d" >> "$L/miss.tsv"; fi; }
export -f one; export OUT L KIND
: > "$L/miss.tsv"
echo "$(date -u +%T) старт: $(wc -l < "$L/tasks.txt") задач" | tee -a "$L/run.log"
xargs -a "$L/tasks.txt" -P "${PAR:-10}" -L 1 bash -c 'one "$@"' _
echo "$(date -u +%T) файлов $(find "$OUT" -name '*.zip' | wc -l), не-200/sha: $(wc -l < "$L/miss.tsv"), размер $(du -sh "$OUT" | cut -f1)" | tee -a "$L/run.log"
touch "$L/done"
