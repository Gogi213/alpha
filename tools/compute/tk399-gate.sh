#!/usr/bin/env bash
# TK-399: гейт `lob seconds --bbo-events` на calc, 3 монето-суток (BTCUSDT фьюч 16.10, ACUUSDT фьюч 15.10, B3USDT спот 17.10).
#   (а) base (a032e302) и new: посекундный CSV байт в байт (cmp);  (б) bbo-вывод new против посекундного ряда (tk399-check.py: mid2 на
#   конце секунды и на +100 мс, все 86 400 секунд);  (в) справка: new против готового /data/tk0369/mid (zcat | cmp), если файл есть.
#   Замер wall/CPU/RSS каждого прогона — для --est боевого прохода (tk399-prod.sh).
# Подготовка: push-head.sh → /data/tk0399/{base,new}; calc-build.sh и скрипты — в /data/tk0399; сборки — отдельные задания
# (tk399-build.sh <v>), гейт ждёт оба бинарника. Итог: /data/tk0399/gate-summary.txt, метка gate.done.
set -uo pipefail
W=/data/tk0399; S=$W/gate-summary.txt; : > "$S"; rm -f "$W/gate.done"
RF=/srv/alpha-sda/tk366w/bin-fut/e-2026-10; RS=/srv/alpha-sda/tk366w/bin-spot/e-2026-10
for i in $(seq 1 240); do [ -x "$W/base/alpha-new" ] && [ -x "$W/new/alpha-new" ] && break; sleep 15; done
if ! { [ -x "$W/base/alpha-new" ] && [ -x "$W/new/alpha-new" ]; }; then echo "нет бинарника base/new — сборка не готова" | tee -a "$S"; exit 70; fi
for v in base new; do echo "$v $("$W/$v/alpha-new" --version) md5 $(md5sum "$W/$v/alpha-new" | cut -c1-32)" >> "$S"; done
fail=0
while read -r cls sym day R; do
  d=$W/gate/$cls-$sym-$day; rm -rf "$d"; mkdir -p "$d/a" "$d/b" "$d/e"
  /usr/bin/time -f "%e %U %S %M" -o "$d/time-a" "$W/base/alpha-new" lob seconds --root "$R" --symbol "$sym" --day "$day" --out "$d/a" > "$d/a.log" 2>&1; rca=$?
  /usr/bin/time -f "%e %U %S %M" -o "$d/time-b" "$W/new/alpha-new" lob seconds --root "$R" --symbol "$sym" --day "$day" --out "$d/b" > "$d/b.log" 2>&1; rcb=$?
  /usr/bin/time -f "%e %U %S %M" -o "$d/time-e" "$W/new/alpha-new" lob seconds --bbo-events --root "$R" --symbol "$sym" --day "$day" --out "$d/e" > "$d/e.log" 2>&1; rce=$?
  cmp -s "$d/a/$sym-$day.csv" "$d/b/$sym-$day.csv"; ga=$?
  python3 "$W/tk399-check.py" "$d/b/$sym-$day.csv" "$d/e/$sym-$day.csv" "$day" > "$d/check.txt" 2>&1; gb=$?
  ref=/data/tk0369/mid/$cls/$day/$sym-$day.csv.gz; gc=na
  if [ -s "$ref" ]; then zcat "$ref" | cmp -s - "$d/b/$sym-$day.csv"; gc=$?; fi
  echo "$cls $sym $day rc=$rca/$rcb/$rce (а)cmp=$ga (б)=$gb (в)ref=$gc | $(cat "$d/check.txt") | bbo_bytes=$(stat -c %s "$d/e/$sym-$day.csv") | wall,user,sys,rssKB base=$(cat "$d/time-a") new=$(cat "$d/time-b") bbo=$(cat "$d/time-e")" >> "$S"
  [ "$rca$rcb$rce$ga$gb" = 00000 ] || fail=1
done <<EOT
fut BTCUSDT 2026-10-16 $RF
fut ACUUSDT 2026-10-15 $RF
spot B3USDT 2026-10-17 $RS
EOT
echo "ИТОГ гейта: $([ $fail = 0 ] && echo ЗЕЛЁНЫЙ || echo КРАСНЫЙ)" >> "$S"
touch "$W/gate.done"; exit $fail
