#!/bin/bash
# TK-428 (возврат Судьи 2, п.3): свечи 1 м Bybit по символам публичного архива с файлами в 01–07.2026, которых нет в списке свечей
# (bybit-archive-extra.tsv от tk428-bybit-index.py: спот-снятые, inverse, события WC_*). REST kline отдаёт и снятые с торгов (проба AFCUSDT, 28.10).
# Раскладка как у основных: $ROOT/m/<ГГГГММ>/{candles,cov}; охват — $ROOT/coverage-<ГГГГММ>.csv (tk411-finalize.py).
# Использование: alsched submit --kind net --name tk428-bybit-extra -- bash /data/tk0428/src/tk428-bybit-extra.sh
set -u
root=${ROOT:-/srv/alpha-sda/tk428y}; here=$(dirname "$(readlink -f "$0")"); ex=${EX:-/srv/alpha-sda/tk428x/bybit-index/bybit-archive-extra.tsv}
mkdir -p "$root/log"
# рынок символ: linear(trading) -> inverse, если символ оканчивается на USD или USD[HMUZ]ГГ; иначе linear (WC_*); spot как есть
awk -F'\t' 'NR>1{c=$1; if(c=="linear" && ($2 ~ /USD$/ || $2 ~ /USD[HMUZ][0-9][0-9]$/)) c="inverse"; print c"\t"$2}' "$ex" > "$root/list.tsv"
while read -r m a b; do
  r=$root/m/$m; mkdir -p "$r/log" "$r/cov"
  K_LIST=$root/list.tsv K_START=$a K_END=$b python3 -I "$here/tk411-bybit-kline.py" "$r" 16 80 >> "$root/log/kline-$m.log" 2>&1
  echo "kline-$m rc=$? n=$(wc -l < "$root/list.tsv") $(date +%T)" >> "$root/log/run.log"
  [ -e "$r/log/miss.tsv" ] || : > "$r/log/miss.tsv"
  [ -e "$r/cov/bybit-instruments.tsv" ] || printf 'рынок\tсимвол\tlaunch_ms\tdelivery_ms\tstatus\tв_окне\n' > "$r/cov/bybit-instruments.tsv"
  K_START=$a K_END=$b python3 -I "$here/tk411-finalize.py" "$r" "$r/log/miss.tsv" "$root/coverage-$m.csv" > "$root/log/finalize-$m.out" 2>&1
  echo "finalize-$m rc=$? $(date +%T)" >> "$root/log/run.log"
done <<'M'
202601 1767225600000 1769904000000
202602 1769904000000 1772323200000
202603 1772323200000 1775001600000
202604 1775001600000 1777593600000
202605 1777593600000 1780272000000
202606 1780272000000 1782864000000
202607 1782864000000 1785542400000
M
touch "$root/done"
