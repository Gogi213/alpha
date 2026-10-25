#!/bin/bash
# TK-392: слои v2/v4 и Solana одной заявкой (приближение: цена исполнения)
set -e
cd /data/tk0380
bash tk392-run4.sh
bash tk392-run5.sh
echo DONE > tk392/run45.done
