#!/usr/bin/env bash
# TK-393 (G-016) докачка 7 (возврат Судьи 25.10): Solana 185 mint (пайплайн TK-389 tk387-sqd-sol.py, окно 15.10–22.10 UTC),
# Base Uniswap v4 (PoolManager+poolId, tk393-sqd-v4b.py), старые Polygon/Optimism/Avalanche (tk380-sqd-evm.py + parse).
# Выход /data/tk0393/sqd7/. Нужны в /data/tk0393: pools-old.csv (=tk380-pools), mints-tk393-sol.csv, tk387-sqd-sol.py, tk378-sol-swaps2.py, tk393-sqd-v4b.py.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; T0=1792022400; T1=1792713599
mkdir -p "$R"; cd "$S" || exit 1
echo "52616000 52962126" > "$R/v4bcursor"
python3 -u tk393-sqd-v4b.py pools-old.csv "$R/v4bcursor" "$R/base-v4" > "$R/base-v4.log" 2>&1 &
python3 -u tk380-sqd-evm.py polygon-mainnet pools-old.csv polygon "$R" $T0 $T1 > "$R/polygon.log" 2>&1 &
python3 -u tk380-sqd-evm.py optimism-mainnet pools-old.csv optimism "$R" $T0 $T1 > "$R/optimism.log" 2>&1 &
python3 -u tk380-sqd-evm.py avalanche-mainnet pools-old.csv avalanche "$R" $T0 $T1 > "$R/avalanche.log" 2>&1 &
python3 -u tk387-sqd-sol.py mints-tk393-sol.csv "$R/sol" $T0 $((T1+1)) 24 12 > "$R/sol.log" 2>&1 &
wait
for c in polygon optimism avalanche; do
  [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1
done
if [ -f "$R/sol/sqd.rc" ]; then
  mkdir -p "$R/sol/sw"
  for d in $(ls "$R/sol/seg" | grep '\.csv$' | cut -d- -f1 | sort -u); do echo "$d"; done | xargs -P 4 -I{} bash -c 'SRC=gate30k python3 tk378-sol-swaps2.py mints-tk393-sol.csv '"$R"'/sol/sw/{}-swaps.csv '"$R"'/sol/sw/{}-cov.csv '"$R"'/sol/seg/{}-*.csv > '"$R"'/sol/sw/{}.log 2>&1'
fi
echo '{"done": true}' > "$R/ALLDONE.json"
