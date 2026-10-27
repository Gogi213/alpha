#!/bin/bash
# TK-428 п.3–4 (G-017, экзамен янв–июль): Bybit-канал, один за раз (лимит IP 600/5 с): свечи 1 м linear+spot по месяцам
#   (tk411-bybit-kline.py, окно K_START/K_END), затем OI+фандинг linear янв–июль (tk415-oi-funding.py).
# Июль — 01–23.07 (24–31.07 уже в /srv/alpha-sda/tk425/jul, TK-425: не качать дважды).
# Использование: tk428-chain-bybit.sh   (ROOT=/srv/alpha-sda/tk428; скрипты рядом)
set -u
root=${ROOT:-/srv/alpha-sda/tk428}; here=$(dirname "$(readlink -f "$0")"); mkdir -p "$root/log" "$root/bybit-oi"
# месяц  начало_мс  конец_мс(исключая)
while read -r m a b; do
  mkdir -p "$root/m/$m"
  K_START=$a K_END=$b python3 -I "$here/tk411-bybit-kline.py" "$root/m/$m" 16 80 >> "$root/log/kline-$m.log" 2>&1
  echo "kline-$m rc=$? $(date +%T)" >> "$root/log/run.log"
done <<'M'
202601 1767225600000 1769904000000
202602 1769904000000 1772323200000
202603 1772323200000 1775001600000
202604 1775001600000 1777593600000
202605 1777593600000 1780272000000
202606 1780272000000 1782864000000
202607 1782864000000 1784851200000
M
# список linear с окном, пересекающим янв–июль (запуск до 01.08, закрытие не раньше 01.01)
awk -F'\t' 'NR>1 && $1=="linear" && $3<1785542400000 && ($4==0 || $4>=1767225600000) {print "bybit,linear," $2 ",1"}' "$root/m/202601/cov/bybit-instruments.tsv" | sed '1i биржа,рынок,инструмент,минут_с_данными' > "$root/bybit-oi/list.csv"
K_START=1767225600000 K_END=1785542400000 python3 -I "$here/tk415-oi-funding.py" bybit "$root/bybit-oi" "$root/bybit-oi/list.csv" 16 >> "$root/log/bybit-oi.log" 2>&1
echo "bybit-oi rc=$? $(date +%T)" >> "$root/log/run.log"
