#!/bin/bash
# TK-410 П-31 W8: середина v4 (eth 39 + base 1 пулов) из data Swap. Метка v4mid.done. Диапазоны блоков — как у tk380/tk393 v4 (eth 26194139–26251600) и sqd10/base (52686839–52962126)
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410
python3 -u tk410-v4mid.py cover.csv ethereum ethereum-mainnet 0x000000000004444c5dc75cb358380d2e3de08a90 26194139 26251600 mid-v4 > v4mid-eth.log 2>&1 &
python3 -u tk410-v4mid.py cover.csv base base-mainnet 0x498581ff718922c3f8e6a244956af099b2652b2b 52686839 52962126 mid-v4 > v4mid-base.log 2>&1 &
wait
touch v4mid.done
