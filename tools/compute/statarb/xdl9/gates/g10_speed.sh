#!/usr/bin/env bash
# TK-493 F6, G10: замер пар-минут/с ядра на СВОБОДНОМ Deck до волн. Порог плана: 2·10^7 пар-минут/с.
# Набор: 120 панелей Binance с полным спаном (01.10 00:00 .. 04.11 23:59, 50 400 минут), все пары (7 140 заданий).
#  (а) bench (чистое ядро, пустой приёмник): строк (= пар-минут) в секунду, потоки 8/7/4, по 3 повтора;
#  (б) боевой путь run: --cells из 5 клеток (4 группы), одна клетка z, одна клетка с --delay-x 1 (C1), D2 (funding-pp, правило funding),
#      с --mtm-open --cells-out; окно — формирование (сутки 2465..2478, 20 160 мин на задание), результаты не читаются (только время и счётчики).
# Перед замером: ждёт нагрузку < 1,0 (никто не считает); печатает загрузку до и после.
set -u
BN=$HOME/xdl9/bin/statarb-xdl9-02c6a0df
BA=$HOME/xdl9/bin/statarb-tk479-f27c879e
P=$HOME/xdl9/panels/binance; PB=$HOME/xdl9/panels/bybit; F=$HOME/xdl9/funding
G=$HOME/xdl9/gates/g10
mkdir -p "$G"; rm -rf "${G:?}"/*
mkdir -p "$G/bin120" "$G/byb120"
python3 - "$HOME/xdl9/panels/manifest.csv" "$HOME/xdl9/panels" "$G" <<'PY'
import csv, os, sys
man, pan, g = sys.argv[1:4]
rows = [r for r in csv.DictReader(open(man, encoding="utf-8"))
        if r["exchange"] == "binance" and r["panel"] == "1" and r["panel_minutes"] == "50400" and r["panel_first_utc"] == "2026-10-01 00:00"]
rows.sort(key=lambda r: r["symbol"])
names = [r["symbol"] for r in rows[:120]]
byb = {r["symbol"] for r in csv.DictReader(open(man, encoding="utf-8")) if r["exchange"] == "bybit" and r["panel"] == "1" and r["panel_minutes"] == "50400" and r["panel_first_utc"] == "2026-10-01 00:00"}
for s in names:
    os.symlink(os.path.join(pan, "binance", s + ".sap"), os.path.join(g, "bin120", s + ".sap"))
    if s in byb:
        os.symlink(os.path.join(pan, "bybit", s + ".sap"), os.path.join(g, "byb120", s + ".sap"))
with open(os.path.join(g, "jobs_pair.csv"), "w") as w:
    w.write("y,x,from_day,to_day\n")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            w.write(f"{names[i]},{names[j]},2465,2479\n")
with open(os.path.join(g, "jobs_cross.csv"), "w") as w:
    w.write("y,x,from_day,to_day\n")
    for s in names:
        if s in byb:
            w.write(f"{s},{s},2465,2479\n")
print(f"символов Binance с полным спаном: {len(names)} (от {names[0]} до {names[-1]}), из них полный спан и на Bybit: {sum(1 for s in names if s in byb)}; заданий пар {len(names)*(len(names)-1)//2}")
PY
cat > "$G/cells5.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
c0,720,360,,2.0,0.5,inf,720,0
c1,720,360,,1.5,0.3,4.0,0,0
c2,1440,720,,2.5,0.5,inf,1440,97
c3,720,360,1.0,2.0,0.5,3.5,480,0
c4,1440,720,,2.0,0.5,3.0,720,200
EOF
wait_free() { for i in $(seq 1 60); do l=$(cut -d' ' -f1 /proc/loadavg); awk -v l=$l 'BEGIN{exit !(l<1.0)}' && return; sleep 5; done; }
wait_free
echo "нагрузка до замера: $(cat /proc/loadavg); ядер $(nproc); governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null); частота cpu0 $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null) кГц"
# прогрев кэша файлов
"$BN" statarb bench --kind pair --engine kernel --panels "$G/bin120" --symbols 120 --max-jobs 100000 --min-span 2000 --threads 8 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0 > /dev/null 2>&1
echo "== (а) bench pair kernel, 120 символов, все пары"
for bin in N A; do
  b=$BN; [ $bin = A ] && b=$BA
  for th in 8 7 4; do
    for rep in 1 2 3; do
      printf '%s thr=%s rep=%s: ' "$bin" "$th" "$rep"
      "$b" statarb bench --kind pair --engine kernel --panels "$G/bin120" --symbols 120 --max-jobs 100000 --min-span 2000 --threads $th --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0 2>&1 \
        | grep -o 'заданий [0-9]* из [0-9]*, минут [0-9]*, пропусков [0-9]*, сделок [0-9]*, счёт [0-9.]* с\|[0-9.e+]* строк/с, [0-9.]* пар/с' | tr '\n' ' '; echo
    done
  done
done
echo "== (б) боевой путь run (формирование, 20 160 мин на задание)"
NJ=$(( $(wc -l < "$G/jobs_pair.csv") - 1 )); NX=$(( $(wc -l < "$G/jobs_cross.csv") - 1 ))
R() {  # метка, аргументы...
  local tag=$1; shift
  for rep in 1 2 3; do
    "$BN" statarb run --jobs "$G/jobs_pair.csv" --panels "$G/bin120" --min-span 1000 --threads ${TH:-8} --daily-out "$G/tmp.daily.csv" "$@" > "$G/tmp.log" 2>&1
    echo "$tag rep=$rep: $(grep -o 'заданий [0-9]* (посчитано [0-9]*[^)]*)' "$G/tmp.log"), $(grep -o 'загрузка [0-9.]* с, счёт [0-9.]* с' "$G/tmp.log")"
  done
}
R "run --cells 5 клеток (4 группы)" --kind pair --cells "$G/cells5.csv"
R "run одна клетка z (c0)" --kind pair --cell c0 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 720 --refresh 0
R "run одна клетка z + --mtm-open --cells-out" --kind pair --cell c0 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 720 --refresh 0 --mtm-open --cells-out "$G/tmp.cells.csv"
R "run C1 (--delay-x 1), одна клетка" --kind pair --cell c0 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 720 --refresh 0 --delay-x 1
for rep in 1 2 3; do
  "$BN" statarb run --kind funding-pp --rule funding --funding-k 3 --hold-payments 200 --jobs "$G/jobs_cross.csv" --panels "$G/bin120" --panels-b "$G/byb120" --funding-y "$F/binance" --funding-x "$F/bybit" \
    --cell d2 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 1440 --refresh 0 --min-span 1000 --threads 8 --daily-out "$G/tmp.daily.csv" > "$G/tmp.log" 2>&1
  echo "run D2 (funding-pp, правило funding, $NX заданий) rep=$rep: $(grep -o 'заданий [0-9]* (посчитано [0-9]*[^)]*)' "$G/tmp.log"), $(grep -o 'загрузка [0-9.]* с, счёт [0-9.]* с' "$G/tmp.log")"
done
for rep in 1 2 3; do
  "$BN" statarb run --kind cross --jobs "$G/jobs_cross.csv" --panels "$G/bin120" --panels-b "$G/byb120" --cell x --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 720 --refresh 0 --min-span 1000 --threads 8 --daily-out "$G/tmp.daily.csv" > "$G/tmp.log" 2>&1
  echo "run cross z ($NX заданий) rep=$rep: $(grep -o 'заданий [0-9]* (посчитано [0-9]*[^)]*)' "$G/tmp.log"), $(grep -o 'загрузка [0-9.]* с, счёт [0-9.]* с' "$G/tmp.log")"
done
rm -f "$G/tmp.daily.csv" "$G/tmp.cells.csv" "$G/tmp.log"
echo "нагрузка после замера: $(cat /proc/loadavg); другие тяжёлые процессы: $(ps -eo pcpu,args --sort=-pcpu | sed -n 2,4p | cut -c1-80 | tr '\n' '|')"
