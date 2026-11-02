#!/usr/bin/env bash
# TK-486: профиль стадии (1) чтение свечей — срез Binance (каждый 9-й файл): чтение с диска, распаковка, convert.
# Запуск на calc через alsched: bash prof-read.sh <биржа> <потоки>; вывод — строки «стадия: секунды, байты, строки».
set -u
EX="${1:-binance-um}"; TH="${2:-4}"
SRC=/srv/alpha-sda/candles-1m/$EX
W=/data/tk0486/prof-$EX; rm -rf "$W"; mkdir -p "$W/src" "$W/out"
ls "$SRC" | awk 'NR%9==1' | while read -r f; do ln -s "$SRC/$f" "$W/src/$f"; done
N=$(ls "$W/src" | wc -l)
BYTES=$(cd "$SRC" && ls "$W/src" | xargs stat -L -c %s | awk '{s+=$1} END{print s}')
echo "срез: $EX файлов $N, gz байт $BYTES"
t() { local s=$(date +%s.%N); "$@"; local e=$(date +%s.%N); echo "$(echo "$e - $s" | bc)"; }
echo "[1] чтение gz с диска (cat > /dev/null, холодный кэш не гарантирован): $(t bash -c "cat $W/src/* > /dev/null") с"
echo "[2] распаковка (zcat | wc -l, $TH потока, кэш тёплый): $(t bash -c "ls $W/src/* | xargs -P $TH -n 4 sh -c 'zcat \"\$@\" | wc -l' _ > $W/lines.txt") с, строк $(awk '{s+=$1} END{print s}' $W/lines.txt)"
echo "[3] convert csv.gz → .sap, $TH потока:"
/data/tk0484/alpha-run-plain statarb convert --src "$W/src" --out "$W/out" --threads "$TH" 2>&1 | tail -3
echo "[4] размер панелей, байт: $(cat $W/out/*.sap | wc -c)"
echo "[5] загрузка панелей в память (cat > /dev/null, кэш тёплый): $(t bash -c "cat $W/out/*.sap > /dev/null") с"
