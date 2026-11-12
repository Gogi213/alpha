#!/usr/bin/env bash
# TK-387 (G-016): Solana через SQD Portal 02.09–14.10 UTC (все 7 mint разом, 24 сегмента/сутки, NPROC процессов), затем
# свопы по суткам (tk378-sol-swaps2.py) и USD-слой (tk378-sqd-usd.py, нужен $S/solusdt-1m.csv). usage: tk387-sqd-sol-run.sh [NPROC=20]
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0387; R=/srv/alpha-sda/tk387-sqdsol; N=${1:-20}
cd "$S" || exit 1
mkdir -p "$R"
python3 -u tk387-sqd-sol.py mints-tk387-sol.csv "$R" 1788307200 1792022400 24 "$N" >> "$R/run.log" 2>&1 || exit 1
[ -f "$S/solusdt-1m.csv" ] || { echo "нет $S/solusdt-1m.csv" >> "$R/run.log"; exit 2; }
mkdir -p "$R/sw"
for d in $(ls "$R/seg" | grep '\.csv$' | cut -d- -f1 | sort -u); do
  echo "$d"
done | xargs -P 4 -I{} bash -c 'SRC=gate30k python3 tk378-sol-swaps2.py mints-tk387-sol.csv '"$R"'/sw/{}-swaps.csv '"$R"'/sw/{}-cov.csv '"$R"'/seg/{}-*.csv > '"$R"'/sw/{}.log 2>&1'
head -1 "$(ls "$R"/sw/*-swaps.csv | head -1)" > "$R/swaps.csv"
for f in "$R"/sw/*-swaps.csv; do tail -n +2 "$f" >> "$R/swaps.csv"; done
python3 tk378-sqd-usd.py "$R/swaps.csv" "$S/solusdt-1m.csv" "$R/swaps-usd.csv" "$R/usd-summary.csv" > "$R/usd.log" 2>&1
echo 0 > "$R/post.rc"
