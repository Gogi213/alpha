#!/bin/bash
# TK-428 (G-017): вернуть сырьё Gate, удалённое сборкой до слова CEO 28.10 («хранить»): месяц, где есть свеча, а сырья нет.
# Список удалённого -> /data/tk0428/deleted-raw-<ГГГГММ>.tsv (рынок, инструмент, quanto); докачка тем же tk411-gate-dl.sh (готовое пропускает).
# Использование: tk428-redl-raw.sh <ГГГГММ> [PAR=8]   (ROOT=/srv/alpha-sda/tk428). Ждёт конца закачки цепочки, чтобы не складывать PAR (бан при 32).
set -u
mon=$1; par=${2:-8}; root=${ROOT:-/srv/alpha-sda/tk428}; list=${LIST:-/data/tk0393/tk423/gate-list.tsv}; here=$(dirname "$(readlink -f "$0")")
r=$root/m/$mon; out=/data/tk0428/deleted-raw-$mon.tsv
until grep -q '^gate-dl-202606 rc=' "$root/log/run.log"; do sleep 60; done
while read -r k c q; do
  [ -s "$r/candles/$k/$c.csv.gz" ] && [ ! -s "$r/raw/$k/$c.csv.gz" ] && printf '%s\t%s\t%s\n' "$k" "$c" "$q"
done < "$list" > "$out"
echo "redl-$mon deleted=$(wc -l < "$out") $(date +%T)" >> "$root/log/run.log"
ROOT=$r MON=$mon PAR=$par bash "$here/tk411-gate-dl.sh" "$out"
echo "redl-$mon rc=$? raw_fut=$(ls "$r/raw/fut" | grep -c 'csv.gz$') raw_spot=$(ls "$r/raw/spot" | grep -c 'csv.gz$') $(date +%T)" >> "$root/log/run.log"
