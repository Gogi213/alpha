#!/bin/bash
# TK-425 (G-017): Bybit-канал после TK-423 (один канал, лимит IP 600/5 с): ждёт rc TK-423 kline, затем
#   (3) OI+фандинг linear за август (tk415-oi-funding.py, окно K_START/K_END), (5) свечи 1 м 24-31.07 Bybit (TK-427) linear+spot.
# Использование: tk425-chain-bybit.sh <rc-файл TK-423 kline>   (ROOT=/srv/alpha-sda/tk425)
set -u
rc=$1; root=${ROOT:-/srv/alpha-sda/tk425}; here=$(dirname "$(readlink -f "$0")"); mkdir -p "$root/aug-oi" "$root/jul" "$root/log"
until [ -s "$rc" ]; do sleep 30; done
# список linear августа из охвата TK-423 (bybit-instruments.tsv: в_окне=1) -> псевдо-охват для tk415-oi-funding.py
awk -F'\t' 'NR>1 && $1=="linear" && $6==1 {print "bybit,linear," $2 ",1"}' /srv/alpha-sda/tk423/cov/bybit-instruments.tsv | sed '1i биржа,рынок,инструмент,минут_с_данными' > "$root/aug-oi/list.csv"
K_START=1785542400000 K_END=1788220800000 python3 -I "$here/tk415-oi-funding.py" bybit "$root/aug-oi" "$root/aug-oi/list.csv" 16 >> "$root/log/oi-aug.log" 2>&1
echo "oi-aug rc=$? $(date +%T)" >> "$root/log/run.log"
K_START=1784851200000 K_END=1785542400000 python3 -I "$here/tk411-bybit-kline.py" "$root/jul" 16 80 >> "$root/log/kline-jul.log" 2>&1
echo "kline-jul rc=$? $(date +%T)" >> "$root/log/run.log"
