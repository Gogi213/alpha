#!/usr/bin/env bash
# TK-393 (G-016): свопы 15–22.10 НОВЫХ пулов (поиск по контрактам Gate, pools-new.csv) через SQD Portal без ключа.
# EVM v2/v3: tk380-sqd-evm (ETH/BSC/Base/Arb/Polygon/OP/Avax) + разбор; ETH v4: tk380-sqd-v4. Aerodrome v2 (иной topic0), Base v4, Solana, Tron, Sui — не здесь.
# Выход /data/tk0393/sqd/<chain>[-swaps]/, ethereum-v4/, ALLDONE.json. usage: tk393-dl.sh   (на calc нужны /data/tk0393/{pools-new.csv,tk380-sqd-evm.py,tk380-sqd-parse.py,tk380-sqd-v4.py})
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd; T0=1792022400; T1=1792713599
mkdir -p "$R"; cd "$S" || exit 1
run() { python3 -u tk380-sqd-evm.py "$1" pools-new.csv "$2" "$R" $T0 $T1 > "$R/$2.log" 2>&1; }
run ethereum-mainnet ethereum & run binance-mainnet bsc & run base-mainnet base & run arbitrum-one arbitrum &
run polygon-mainnet polygon & run optimism-mainnet optimism & run avalanche-mainnet avalanche &
echo "26194139 26251600" > "$R/v4cursor"
python3 -u tk380-sqd-v4.py pools-new.csv "$R/v4cursor" "$R/ethereum-v4" > "$R/v4.log" 2>&1 &
wait
for c in ethereum bsc base arbitrum polygon optimism avalanche; do
  [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1
done
echo '{"done": true}' > "$R/ALLDONE.json"
