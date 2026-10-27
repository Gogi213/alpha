#!/bin/bash
# TK-411: свечи 1 м Gate из сырья tk411-gate-dl.sh -> /srv/alpha-sda/tk411/candles/{fut,spot}/ИНСТР.csv.gz; охват -> candles/coverage-gate.tsv.
# N потоков ($2, по умолчанию 3); сырьё после сборки удаляется (в тикете «держать только свечи»); готовое пропускается.
#   alsched submit --cls prod --cores 3 --name tk411-gate-build --max-runtime 3h --est 1h -- bash /data/tk0393/tk411/tk411-gate-build.sh /data/tk0393/tk411/gate-list.tsv 3
set -u
list=$1; n=${2:-3}; root=${ROOT:-/srv/alpha-sda/tk411}; here=$(dirname "$(readlink -f "$0")")
mkdir -p "$root/candles/fut" "$root/candles/spot" "$root/cov"
while [ ! -e "$root/dl.done" ]; do sleep 15; done  # закачка tk411-gate-dl.sh ещё идёт
b() {
  k=$1; c=$2; q=$3; root=$4; here=$5
  src=$root/raw/$k/$c.csv.gz; dst=$root/candles/$k/$c.csv.gz
  [ -s "$dst" ] && return 0
  [ -s "$src" ] || return 0
  r=$(python3 -I "$here/tk411-gate-candles.py" "$k" "$src" "$q" "$dst.part") || { rm -f "$dst.part"; echo "FAIL $k $c" >&2; return 0; }
  mv "$dst.part" "$dst"; printf '%s\t%s\t%s\n' "$k" "$c" "$r" > "$root/cov/$k-$c.tsv"; rm -f "$src"
}
export -f b
echo "start $(date +%T) N=$n" >> "$root/log/run.log"
xargs -a "$list" -P"$n" -L1 bash -c 'b "$0" "$1" "$2" '"$root $here"
echo "end rc=$? $(date +%T) candles=$(find "$root/candles" -name '*.csv.gz' | wc -l)" >> "$root/log/run.log"
cat "$root"/cov/*.tsv > "$root/candles/coverage-gate.tsv"
echo DONE > "$root/build.done"
