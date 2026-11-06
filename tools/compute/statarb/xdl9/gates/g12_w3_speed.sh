#!/usr/bin/env bash
# TK-493 w3, G12: цена новых счётчиков и флагов в ядре. Попеременно (A B A B …) эталонный бинарь (BIN_O, 02c6a0df) и новый (BIN_N):
#  (а) bench pair kernel, 120 символов Binance, все пары (7 140 заданий), пустой приёмник — строк/с (чистое ядро);
#  (б) run --cells (5 клеток, 4 группы) на формировании: без новых флагов; новым бинарём — с --rearm; со всеми новыми выводами.
# Использует каталог g10 (панели bin120, jobs_pair.csv, cells5.csv) от g10_speed.sh. Ждёт нагрузку < 1,0.
#   g12_w3_speed.sh [повторов]
set -u
BIN_O=${BIN_O:-$HOME/xdl9/bin/statarb-xdl9-02c6a0df}
BIN_N=${BIN_N:-$HOME/xdl9/bin/statarb-xdl9-6108b0bf}
G=$HOME/xdl9/gates/g10
REP=${1:-5}
wait_free() { for i in $(seq 1 90); do l=$(cut -d' ' -f1 /proc/loadavg); awk -v l=$l 'BEGIN{exit !(l<1.0)}' && return; sleep 5; done; }
wait_free
echo "нагрузка до: $(cat /proc/loadavg); governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
B() { "$1" statarb bench --kind pair --engine kernel --panels "$G/bin120" --symbols 120 --max-jobs 100000 --min-span 2000 --threads 8 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0 2>&1 | grep -o '[0-9.e+]* строк/с' ; }
B "$BIN_O" > /dev/null   # прогрев кэша файлов
echo "== (а) bench pair kernel, 8 потоков: строк/с (эталон O / новый N, попеременно)"
for r in $(seq 1 "$REP"); do echo "rep $r: O $(B "$BIN_O")  N $(B "$BIN_N")"; done
R() {  # бинарь, метка, аргументы...
  local bin=$1 tag=$2; shift 2
  "$bin" statarb run --kind pair --jobs "$G/jobs_pair.csv" --panels "$G/bin120" --min-span 1000 --threads 8 --daily-out "$G/w3tmp.daily.csv" --cells "$G/cells5.csv" "$@" 2>&1 \
    | grep -o 'сделок [0-9]* (до окна отброшено [0-9]*)\|загрузка [0-9.]* с, счёт [0-9.]* с' | tr '\n' ' ' | sed "s/^/$tag: /"; echo
}
echo "== (б) run --cells, 5 клеток × 7 140 заданий, формирование"
for r in $(seq 1 "$REP"); do
  R "$BIN_O" "rep $r O без флагов     "
  R "$BIN_N" "rep $r N без флагов     "
  R "$BIN_N" "rep $r N --rearm        " --rearm
  R "$BIN_N" "rep $r N все выводы     " --ext-out "$G/w3tmp.ext.csv" --job-day-out "$G/w3tmp.jd.csv" --rearm
done
rm -f "$G"/w3tmp.*
echo "нагрузка после: $(cat /proc/loadavg)"
