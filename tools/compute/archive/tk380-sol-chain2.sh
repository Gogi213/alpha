#!/bin/bash
# TK-380 возврат Судьи: после докачки 13 тяжёлых Solana-пулов (sol4 + KIMCHI sol5) — пересчёт свопы->USD->отрывы v3.
cd /data/tk0380
while systemctl is-active --quiet tk380-sol4 || systemctl is-active --quiet tk380-sol5 || systemctl is-active --quiet tk380-sol6 || systemctl is-active --quiet tk380-sol7; do sleep 20; done
for f in sol5/tx-*.jsonl.gz sol6/tx-*.jsonl.gz sol7/tx-*.jsonl.gz; do [ -f "$f" ] && ln -sf /data/tk0380/$f sol/$(basename $f); done
[ -d solout124 ] || mv solout solout124
bash tk380-sol-run.sh > solout-run2.log 2>&1
python3 -u tk380-sol-lag3.py /data/tk0380/lag3-solana2 > lag3-solana2.log 2>&1 && python3 tk380-bsc-lag3-summ.py /data/tk0380/lag3-solana2 /data/tk0380/lag3-solana2/agg.json > /data/tk0380/lag3-solana2/agg.txt 2>&1
echo done > /data/tk0380/lag3-solana2.done
