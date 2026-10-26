#!/bin/bash
# TK-410 добор 6 USD-монет, шаг 1 — закачка SQD Swap+Sync 15-22.10 (net; курсоры sqd11/<chain>/cursor продолжают с места).
# По концу сама подаёт шаг 2 (счёт 15 ядер). cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd11; T0=1792022400; T1=1792713599
cd $S/tk410 || exit 1; mkdir -p "$R"
for pair in bsc:binance-mainnet ethereum:ethereum-mainnet polygon:polygon-mainnet avalanche:avalanche-mainnet; do
  c=${pair%%:*}; ds=${pair##*:}
  python3 -u tk410-sqd-evm.py $ds rest-pools.csv $c "$R" $T0 $T1 >> "$R/$c.log" 2>&1 &
done
wait
echo rc=$? > rest-dl.done
python3 /data/sched/alsched.py submit --name tk410-rest-proc --max-runtime 1h --est 25m --cores 15 --cwd $S/tk410 -- bash $S/tk410/tk410-rest-proc.sh > rest-proc-submit.log 2>&1
