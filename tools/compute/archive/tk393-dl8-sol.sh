#!/usr/bin/env bash
# TK-393 докачка 8: Solana 185 mint (tk387-sqd-sol.py, готовые сегменты .done пропускает) + разбор свопов. Заявка --mem 20 (dl8 rc143: oom при 12 процессах в 8 ГБ, теперь 8 процессов).
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; T0=1792022400; T1=1792713599
cd "$S" || exit 1
python3 -u tk387-sqd-sol.py mints-tk393-sol.csv "$R/sol" $T0 $((T1+1)) 24 8 >> "$R/sol.log" 2>&1
if [ -f "$R/sol/sqd.rc" ]; then
  mkdir -p "$R/sol/sw"
  for d in $(ls "$R/sol/seg" | grep '\.csv$' | cut -d- -f1 | sort -u); do echo "$d"; done | xargs -P 4 -I{} bash -c 'SRC=gate30k python3 tk378-sol-swaps2.py mints-tk393-sol.csv '"$R"'/sol/sw/{}-swaps.csv '"$R"'/sol/sw/{}-cov.csv '"$R"'/sol/seg/{}-*.csv > '"$R"'/sol/sw/{}.log 2>&1'
fi
echo '{"done": true}' > "$R/SOLDONE.json"
