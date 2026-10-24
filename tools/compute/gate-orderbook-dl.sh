#!/bin/bash
# TK-379: стакан фьючерса Gate (download.gatedata.org/futures_usdt/orderbooks/YYYYMM/КОНТРАКТ-YYYYMMDDHH.csv.gz),
# 15–22.10 UTC, 4 потока, готовые файлы пропускаются, не-200 → log/miss.tsv (контракт, час, код).
# Порядок — по КОНТРАКТАМ из списка (первые в списке качаются первыми), внутри контракта — все часы подряд.
# usage: gate-orderbook-dl.sh <список контрактов в порядке приоритета> <корень>
# Доп. запуск рядом с идущим (проба потоков, TK-379): TAG=p P=4 — берёт готовый tasks2.txt С КОНЦА, свой суффикс .part<TAG>,
# свои miss<TAG>.tsv и codes<TAG>.tsv (каждый не-200/404 ответ с номером попытки — видно 429/5xx, curl --retry их прячет).
set -u
list=$1; root=$2
dir=$root/futures_usdt/orderbooks/202610; mkdir -p "$dir" "$root/log"
free=$(df -BG --output=avail "$root" | tail -1 | tr -dc 0-9)
[ "$free" -ge 250 ] || { echo "мало места: ${free} ГБ" >&2; exit 2; }
tasks=$root/log/tasks2.txt
if [ -n "${TAG:-}" ]; then tac "$tasks" > "$root/log/tasks2$TAG.txt"; tasks=$root/log/tasks2$TAG.txt
else
while read -r c; do for d in ${DAYS:-15 16 17 18 19 20 21 22}; do for h in $(seq -w 0 23); do
  echo "$c 202610$d$h"
done; done; done < "$list" > "$tasks"
fi
dl() {
  c=$1; h=$2; f=$3/$c-$h.csv.gz
  [ -s "$f" ] && return 0
  if [ -n "${TAG:-}" ]; then
    for try in 1 2 3 4 5 6; do
      code=$(curl -s -m 600 -o "$f.part$TAG" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz")
      if [ "$code" = 200 ] && gzip -t "$f.part$TAG" 2>/dev/null; then mv "$f.part$TAG" "$f"; return 0; fi
      [ "$code" = 404 ] && break
      printf '%s\t%s\t%s\t%s\t%s\n' "$(date +%T)" "$c" "$h" "$code" "$try" >> "$4/log/codes$TAG.tsv"; sleep $((try*10))
    done
    rm -f "$f.part$TAG"; printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss$TAG.tsv"; return 0
  fi
  code=$(curl -sf -m 600 --retry 6 --retry-delay 10 -o "$f.part" -w '%{http_code}' \
    "https://download.gatedata.org/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"
  else rm -f "$f.part"; printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss.tsv"; fi
}
export -f dl
echo "tk379-ob2$TAG start $(date +%T) tasks=$(wc -l < "$tasks")" >> "$root/log/run.log"
xargs -a "$tasks" -P${P:-4} -L1 bash -c 'dl "$0" "$1" '"$dir $root"
echo "tk379-ob2$TAG end rc=$? $(date +%T) files=$(ls "$dir" | grep -c 'csv.gz$') miss=$(wc -l < "$root/log/miss$TAG.tsv" 2>/dev/null)" >> "$root/log/run.log"
