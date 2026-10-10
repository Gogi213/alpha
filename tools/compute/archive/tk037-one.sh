#!/bin/bash
# TK-037: одна монето-сутки: скачать ob200-zip + сделки в RAW (хранится), импорт --steps-from-snapshot в <ROOT>/e-YYYY-MM.
#   tk037-one.sh SYM DAY   env: RAW ROOT BIN INSTR LOG   Строка в $LOG: sym day ob_bytes tr_bytes dl_s imp_wall imp_user imp_sys rc
sym=$1; d=$2; mon=${d:0:7}
NC=${NC:-0}   # NC=1: после записи/чтения сбрасывать страницы файлов из кэша (счёт на том же сервере держит окно в page cache)
cdrop() { [ "$NC" = 1 ] && for f in "$@"; do [ -e "$f" ] && { sync -d "$f"; dd if="$f" of=/dev/null iflag=nocache count=0 2>/dev/null; }; done; return 0; }
RAW=${RAW:-/data/tk037raw}; ROOT=${ROOT:-/data/tk037/roots}; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk035-steps}
INSTR=${INSTR:-/data/tk037/instruments.csv}; LOG=${LOG:-/data/tk037/one.log}
out=$ROOT/e-$mon; mkdir -p "$RAW" "$out"
[ -f "$out/$sym-$d.binlog" ] && { echo "$sym $d skip-have" >> "$LOG"; exit 0; }
ob=$RAW/ob-$sym-$d.zip; tr=$RAW/tr-$sym-$d.csv.gz
t0=$(date +%s.%N)
if [ ! -s "$ob" ]; then curl -sf --retry 3 --max-time 1800 -o "$ob.part" "https://quote-saver.bycsi.com/orderbook/linear/$sym/${d}_${sym}_ob200.data.zip" && mv "$ob.part" "$ob" || { rm -f "$ob.part"; echo "$sym $d missing-ob" >> "$LOG"; exit 0; }; fi
if [ ! -s "$tr" ]; then curl -sf --retry 3 --max-time 1800 -o "$tr.part" "https://public.bybit.com/trading/$sym/$sym$d.csv.gz" && mv "$tr.part" "$tr" || { rm -f "$tr.part"; echo "$sym $d missing-tr" >> "$LOG"; exit 0; }; fi
cdrop "$ob" "$tr"
t1=$(date +%s.%N)
tm=$(mktemp)
if [ "${DAYMODE:-0}" = 1 ]; then   # DAYMODE=1: --steps-from-day (смена шага внутри суток), --ob файлом
  unzip -p "$ob" > "$tm.ob"
  nice -n 10 /usr/bin/time -f "%e %U %S" -o "$tm" $BIN lob import-archive --symbol $sym --day $d --ob "$tm.ob" --trades <(gunzip -c "$tr") --instruments "$INSTR" --root "$out" --steps-from-day > "$tm.out" 2>&1
  rm -f "$tm.ob"
else
  unzip -p "$ob" | nice -n 10 /usr/bin/time -f "%e %U %S" -o "$tm" $BIN lob import-archive --symbol $sym --day $d --ob - --trades <(gunzip -c "$tr") --instruments "$INSTR" --root "$out" --steps-from-snapshot > "$tm.out" 2>&1
fi
rc=$?
[ $rc -ne 0 ] && rm -f "$out/$sym-$d.binlog" "$out/$sym-$d.binlog.part"
cdrop "$ob" "$tr" "$out/$sym-$d.binlog" "$out/$sym-$d.binlog.events"
echo "$sym $d $(stat -c%s "$ob") $(stat -c%s "$tr") $(echo "$t1 - $t0" | bc) $(cat "$tm") rc=$rc $(tail -1 "$tm.out" | cut -c1-200)" >> "$LOG"
rm -f "$tm" "$tm.out"
