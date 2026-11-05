#!/usr/bin/env bash
# TK-493 F4: свечи 8 минутных бирж xdl → панели .sap (statarb convert) + фандинг для ядра + сверка (Steam Deck).
#   convert_xdl9.sh [фаза…]   фазы: farm convert funding sweep gate manifest core sha (по умолчанию все по порядку)
# Запуск тяжёлого — только юнитом: systemd-run --user --unit=xdl9-convert --collect bash convert_xdl9.sh
# Переменные: W (рабочий каталог, ~/xdl9), ROOT (~/xdl/out), BIN (бинарь convert), TH (потоки convert, 8), PROCS (python, 8),
#             T (каталог tools/compute/statarb на машине), EXS (биржи через пробел).
# Сырьё не трогается: src/ и funding/ — симлинки; повторный запуск пересоздаёт только panels/<биржа> и verify/.
set -uo pipefail
W="${W:-$HOME/xdl9}"; ROOT="${ROOT:-$HOME/xdl/out}"; BIN="${BIN:-$W/bin/statarb-tk479-f27c879e}"
TH="${TH:-8}"; PROCS="${PROCS:-8}"; T="${T:-$W/tools/statarb}"
EXS=(${EXS:-binance bybit okx bingx bitget kraken aster lighter}); EXL=$(IFS=,; echo "${EXS[*]}")
X="$T/xdl9"
mkdir -p "$W/log" "$W/panels" "$W/verify"
PH=("$@"); [ ${#PH[@]} -gt 0 ] || PH=(farm convert funding sweep gate manifest core sha)
for ph in "${PH[@]}"; do
  echo "== фаза $ph $(date +%T)"
  case "$ph" in
    farm) python3 "$X/panels_xdl9.py" farm --root "$ROOT" --work "$W" --exchanges "$EXL" 2>&1 | tee "$W/log/farm.log" ;;
    convert)
      for ex in "${EXS[@]}"; do
        rm -rf "$W/panels/$ex"; mkdir -p "$W/panels/$ex"
        t0=$(date +%s.%N)
        "$BIN" statarb convert --src "$W/src/$ex" --out "$W/panels/$ex" --threads "$TH" > "$W/log/convert-$ex.log" 2>&1; rc=$?
        t1=$(date +%s.%N)
        echo "$rc $t0 $t1 $TH" > "$W/log/convert-$ex.time"
        echo "-- $ex rc=$rc: $(grep '^convert:' "$W/log/convert-$ex.log")"
        grep '^ошибка' "$W/log/convert-$ex.log" | head -5
      done ;;
    funding) python3 "$X/panels_xdl9.py" funding-check --work "$W" --out "$W/verify/funding_check.csv" --procs "$PROCS" --exchanges "$EXL" 2>&1 | tee "$W/log/funding-check.log" ;;
    sweep) python3 "$X/verify_xdl9.py" sweep --work "$W" --out "$W/verify/sweep.csv" --procs "$PROCS" --exchanges "$EXL" 2>&1 | tee "$W/log/sweep.log" ;;
    gate) python3 "$X/verify_xdl9.py" gate --root "$ROOT" --work "$W" --sweep "$W/verify/sweep.csv" --out "$W/verify/gate.csv" --exchanges "$EXL" 2>&1 | tee "$W/log/gate.log" ;;
    manifest) python3 "$X/panels_xdl9.py" manifest --root "$ROOT" --work "$W" --sweep "$W/verify/sweep.csv" --funding-check "$W/verify/funding_check.csv" --exchanges "$EXL" 2>&1 | tee "$W/log/manifest.log" ;;
    core)
      # «Ядро принимает»: загрузка ВСЕХ панелей и ВСЕГО фандинга парами бирж без счёта (--max-jobs 0: заданий и сделок нет;
      # остальные параметры формальные). Число «ставок фандинга» в выводе = сумма строк фандинга символов с панелью обеих бирж.
      : > "$W/log/core-load.log"
      for pr in binance:bybit okx:bingx bitget:kraken aster:lighter; do
        a=${pr%%:*}; b=${pr##*:}
        echo "-- $a + $b" | tee -a "$W/log/core-load.log"
        "$BIN" statarb bench --kind funding-pp --engine kernel --panels "$W/panels/$a" --panels-b "$W/panels/$b"           --funding-y "$W/funding/$a" --funding-x "$W/funding/$b" --symbols 1000000 --max-jobs 0 --min-span 1440           --threads "$TH" --window 240 --min-obs 240 --entry-z 2 --exit-z 0 --max-hold 480 --refresh 240 2>&1 | tee -a "$W/log/core-load.log"
      done ;;
    sha)
      # Контрольные суммы панелей: sha256-<биржа>.txt (по файлу) и свод по бирже и по всему набору — для сверки повторной конвертации.
      for ex in "${EXS[@]}"; do
        (cd "$W/panels/$ex" && ls | LC_ALL=C sort | xargs -P "$PROCS" -n 40 sha256sum | LC_ALL=C sort -k2) > "$W/panels/sha256-$ex.txt"
        echo "$ex $(wc -l < "$W/panels/sha256-$ex.txt") файлов, свод $(sha256sum < "$W/panels/sha256-$ex.txt" | cut -c1-16)"
      done
      echo "весь набор: свод $(cat "$W"/panels/sha256-*.txt | LC_ALL=C sort -k2 | sha256sum | cut -c1-16)" ;;
    *) echo "неизвестная фаза $ph"; exit 2 ;;
  esac
done
echo "== готово $(date +%T)"
