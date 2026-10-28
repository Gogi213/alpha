#!/bin/bash
# TK-428 (возврат Судьи, п.2 — выжившие): Gate-инструменты с архивом сделок в 01–07.2026, которых нет в gate-list (делистнуты до августа):
# сырьё ХРАНИТСЯ (KEEP_RAW=1, CEO 28.10) -> /srv/alpha-sda/tk428x/m/<ГГГГММ>/{raw,candles}; охват -> tk428x/coverage-extra-<ГГГГММ>.csv.
# Входы в /data/tk0428: gate-missing.tsv (рынок мес инструмент байт — из S3-листинга tk428-gate-index.py без gate-list),
# gate-extra-list.tsv (рынок инструмент quanto — tk428-gate-quanto.py; фьючерсы без ответа API в список не входят).
# Использование: tk428-gate-extra.sh dl|build   (ROOT=/srv/alpha-sda/tk428x; PAR=8).
#   alsched submit --kind net   --name tk428-extra-dl    -- bash /data/tk0428/src/tk428-gate-extra.sh dl
#   alsched submit --kind build --cores 1 --name tk428-extra-build -- bash /data/tk0428/src/tk428-gate-extra.sh build
set -u
export KEEP_RAW=1
mode=${1:?dl|build}; root=${ROOT:-/srv/alpha-sda/tk428x}; d=${D:-/data/tk0428}; here=$(dirname "$(readlink -f "$0")")
mkdir -p "$root/log"
# месяц  начало_мс  конец_мс (UTC, как в tk428-final.sh; июль — весь месяц)
M="202601 1767225600000 1769904000000
202602 1769904000000 1772323200000
202603 1772323200000 1775001600000
202604 1775001600000 1777593600000
202605 1777593600000 1780272000000
202606 1780272000000 1782864000000
202607 1782864000000 1785542400000"
echo "$M" | while read -r m a b; do
  r=$root/m/$m; mkdir -p "$r/log" "$r/cov"
  awk -F'\t' -v m="$m" 'NR==FNR{q[$1"\t"$2]=$3;next} $2==m && q[$1"\t"$3]!=""{print $1"\t"$3"\t"q[$1"\t"$3]}' "$d/gate-extra-list.tsv" "$d/gate-missing.tsv" > "$r/list.tsv"
  if [ "$mode" = dl ]; then
    ROOT=$r MON=$m PAR=${PAR:-8} bash "$here/tk411-gate-dl.sh" "$r/list.tsv"
    echo "extra-dl-$m rc=$? n=$(wc -l < "$r/list.tsv") $(date +%T)" >> "$root/log/run.log"
  else
    ROOT=$r bash "$here/tk411-gate-build.sh" "$r/list.tsv" 1
    echo "extra-build-$m rc=$? $(date +%T)" >> "$root/log/run.log"
    [ -e "$r/log/miss.tsv" ] || : > "$r/log/miss.tsv"
    [ -e "$r/cov/bybit-instruments.tsv" ] || printf 'рынок\tсимвол\tlaunch_ms\tdelivery_ms\tstatus\tв_окне\n' > "$r/cov/bybit-instruments.tsv"
    K_START=$a K_END=$b python3 -I "$here/tk411-finalize.py" "$r" "$r/log/miss.tsv" "$root/coverage-extra-$m.csv" > "$root/log/finalize-$m.out" 2>&1
    echo "extra-finalize-$m rc=$? $(date +%T)" >> "$root/log/run.log"
  fi
done
echo "$mode rc=0 $(date +%T)" >> "$root/log/run.log"; touch "$root/$mode.done"
