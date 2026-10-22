#!/usr/bin/env bash
# TK-366 (G-016): одна монето-сутки публичного архива Bybit → бинлог v3/v4 (--steps-from-day), сырьё удаляется сразу.
#   tk366-one.sh fut|spot SYM DAY    env: BIN ROOT(корень выхода; e-YYYY-MM внутри) TMP LOG INSTR(только fut)
# Журнал $LOG (tsv): mk sym day status ob_bytes tr_bytes dl_s imp_s binlog_bytes сообщение   status = ok|skip|missing-ob|missing-tr|fail:rc
# Нет файла на бирже (404) — missing, без достройки (В-172). Готовое пропускается. Сеть и импорт — в одном шаге, чтобы сырьё не копилось.
mk=$1; sym=$2; d=$3; mon=${d:0:7}
BIN=${BIN:?}; ROOT=${ROOT:?}; TMP=${TMP:-/dev/shm/tk366}; LOG=${LOG:-$ROOT/import.tsv}
out=$ROOT/e-$mon; mkdir -p "$out" "$TMP"
b="$out/$sym-$d.binlog"; ob="$TMP/$mk-$sym-$d.zip"; tr="$TMP/$mk-$sym-$d.csv.gz"; tb="$TMP/$mk-$sym-$d.ob"; tt="$TMP/$mk-$sym-$d.csv"
log() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mk" "$sym" "$d" "$1" "${2:-0}" "${3:-0}" "${4:-0}" "${5:-0}" "${6:-0}" "${7:-}" >> "$LOG"; }
[ -f "$b" ] && { log skip; exit 0; }
if [ "$mk" = fut ]; then
  uo="https://quote-saver.bycsi.com/orderbook/linear/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/trading/$sym/$sym$d.csv.gz"
  extra=(--instruments "${INSTR:?}")
else
  uo="https://quote-saver.bycsi.com/orderbook/spot/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/spot/$sym/${sym}_$d.csv.gz"
  extra=(--spot)
fi
t0=$(date +%s)
curl -sf --retry 3 --max-time 3600 -o "$ob" "$uo" || { rm -f "$ob"; log missing-ob; exit 0; }
curl -sf --retry 3 --max-time 3600 -o "$tr" "$ut" || { rm -f "$ob" "$tr"; log missing-tr; exit 0; }
t1=$(date +%s); rc=0; msg=""
unzip -p "$ob" > "$tb" && gunzip -c "$tr" > "$tt" &&
  msg=$(nice -n 10 "$BIN" lob import-archive --symbol "$sym" --day "$d" --ob "$tb" --trades "$tt" --root "$out" --steps-from-day "${extra[@]}" 2>&1)
rc=$?
[ $rc -ne 0 ] && rm -f "$b" "$b.part"
log "$([ $rc = 0 ] && echo ok || echo fail:$rc)" "$(stat -c%s "$ob")" "$(stat -c%s "$tr")" $((t1-t0)) $(( $(date +%s)-t1 )) "$(stat -c%s "$b" 2>/dev/null || echo 0)" "$(echo "$msg" | tr '\n\t' '  ' | cut -c1-300)"
rm -f "$ob" "$tr" "$tb" "$tt"
