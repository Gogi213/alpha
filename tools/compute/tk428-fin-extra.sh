#!/bin/bash
# TK-428: пересчёт охвата tk428y (список вне instruments-info: запуск/закрытие неизвестны -> la=0, de=0, окно = весь месяц)
set -u
root=/srv/alpha-sda/tk428y; here=/data/tk0428/src
while read -r m a b; do
  r=$root/m/$m; w=$(( (b-a)/60000 ))
  { printf 'рынок\tсимвол\tlaunch_ms\tdelivery_ms\tstatus\tв_окне\n'; awk -F'\t' -v w=$w '{print $1"\t"$2"\t0\t0\tвне_списка_архив\t"w}' $root/list.tsv; } > $r/cov/bybit-instruments.tsv
  K_START=$a K_END=$b python3 -I $here/tk411-finalize.py $r $r/log/miss.tsv $root/coverage-$m.csv > $root/log/finalize-$m.out 2>&1
  echo "finalize2-$m rc=$? $(date +%T)" >> $root/log/run.log
done <<'M'
202601 1767225600000 1769904000000
202602 1769904000000 1772323200000
202603 1772323200000 1775001600000
202604 1775001600000 1777593600000
202605 1777593600000 1780272000000
202606 1780272000000 1782864000000
202607 1782864000000 1785542400000
M
