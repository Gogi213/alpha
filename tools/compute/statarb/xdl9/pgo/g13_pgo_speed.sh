#!/usr/bin/env bash
# TK-493 pgo, G13: скорость ядра — не-PGO эталон (BIN_O, по умолчанию 4ef45cc8) против PGO (BIN_P) и, если задан, контроль v3+LTO без PGO (BIN_V).
# Запуск на Steam Deck. Попеременно O P [V] O P [V] … (повторов REP, по умолчанию 5), без nice; нагрузка Deck пишется до/после и по повторам.
# Панели — ФОРМИРОВАНИЕ (prep_pform.py: обрезка по суткам < 2479), торговое окно не читается.
#   (а) bench pair kernel, bin120 (120 символов Binance, 7 140 пар, 20 160 минут), пустой приёмник — строк (пар-минут) в секунду; TH потоков (по умолчанию 4) и 1 поток;
#   (б) run --cells (5 клеток × 7 140 заданий, g10/jobs_pair.csv): без флагов; с --rearm --trades-ext --ext-out --job-day-out; одноклеточный проход (--delay-x 2).
#   g13_pgo_speed.sh [повторов]
set -u
REP=${1:-5}
TH=${TH:-4}
ONLY_B=${ONLY_B:-}     # непусто — только часть (б)
JOBS_N=${JOBS_N:-}     # непусто — в (б) первые JOBS_N заданий g10/jobs_pair.csv (меньше времени при 1 потоке)   # потоки основного замера (на Deck другие сессии; ограничение ресурсов — не более 4)
BIN_O=${BIN_O:-$HOME/xdl9/bin/statarb-xdl9-4ef45cc8}
BIN_P=${BIN_P:-$HOME/xdl9/bin/statarb-xdl9-4ef45cc8-pgo}
BIN_V=${BIN_V:-}
PF=$HOME/xdl9/pgo/pform; G=$HOME/xdl9/gates/g10; T=${G13_TMP:-$HOME/xdl9/pgo/g13tmp}
mkdir -p "$T"
BINS=(O P); [ -n "$BIN_V" ] && BINS=(O P V)
bin_of() { case $1 in O) echo "$BIN_O";; P) echo "$BIN_P";; V) echo "$BIN_V";; esac; }
load() { cut -d' ' -f1-3 /proc/loadavg; }
wait_free() { for i in $(seq 1 120); do l=$(cut -d' ' -f1 /proc/loadavg); awk -v l=$l 'BEGIN{exit !(l<1.0)}' && return; sleep 5; done; }
wait_free
echo "нагрузка до: $(cat /proc/loadavg); governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null); потоков в системе $(nproc)"
echo "O=$BIN_O"; echo "P=$BIN_P"; [ -n "$BIN_V" ] && echo "V=$BIN_V"
B() {  # бинарь, потоки, символов
  "$1" statarb bench --kind pair --engine kernel --panels "$PF/bin120" --symbols "$3" --max-jobs 100000 --min-span 2000 --threads "$2" \
    --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0 2>&1 | grep -o '[0-9.e+]* строк/с, [0-9.]* пар/с'
}
for t in O P V; do [ "$t" = V ] && [ -z "$BIN_V" ] && continue; B "$(bin_of $t)" "$TH" 40 > /dev/null; done   # прогрев кэша файлов
if [ -z "$ONLY_B" ]; then
echo "== (а) bench pair kernel, 120 символов: строк/с (пар-минут/с), попеременно ${BINS[*]}"
for th in $TH 1; do
  sym=120; [ "$th" = 1 ] && sym=60
  for r in $(seq 1 "$REP"); do
    line="th=$th rep $r [load $(load)]:"
    for t in "${BINS[@]}"; do line="$line $t $(B "$(bin_of $t)" "$th" "$sym")  |"; done
    echo "$line"
  done
done
fi
JOBS=$G/jobs_pair.csv
[ -n "$JOBS_N" ] && { JOBS=$T/jobs_head.csv; head -n $((JOBS_N+1)) "$G/jobs_pair.csv" > "$JOBS"; }
R() {  # бинарь, метка, аргументы...
  local bin=$1 tag=$2; shift 2
  "$bin" statarb run --kind pair --jobs "$JOBS" --panels "$PF/bin120" --min-span 1000 --threads "$TH" --daily-out "$T/w.daily.csv" --cells "$G/cells5.csv" "$@" 2>&1 \
    | grep -o 'сделок [0-9]* (до окна отброшено [0-9]*)\|загрузка [0-9.]* с, счёт [0-9.]* с' | tr '\n' ' ' | sed "s/^/$tag: /"; echo
}
echo "== (б) run --cells, 5 клеток × ${JOBS_N:-7140} заданий, потоков $TH, формирование"
for r in $(seq 1 "$REP"); do
  for t in "${BINS[@]}"; do
    b=$(bin_of $t)
    R "$b" "rep $r $t без флагов [load $(load)]"
    R "$b" "rep $r $t --rearm + все выводы  " --rearm --trades-ext --trades-out "$T/w.trades.csv" --ext-out "$T/w.ext.csv" --job-day-out "$T/w.jd.csv"
    R "$b" "rep $r $t --delay-x 2 (одноклеточный путь)" --delay-x 2
  done
done
rm -rf "$T"
echo "нагрузка после: $(cat /proc/loadavg)"
