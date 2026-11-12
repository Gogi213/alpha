#!/bin/bash
# TK-409 возврат Судьи 08:58: LAPTOP (base aerodrome) и SUE (bsc pancake v2) — gev2 LAPTOP, середина из Sync (SQD), S(б)(в), Rq. Метка LS/LS.done
B=/data/tk0393/tk409/LS; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all BASIS=ev
# докачка base (bsc, gev2 LAPTOP готовы; курсор sqd/base/cursor). Метка DL.done
B=/data/tk0393/tk409/LS; S=/data/tk0393
cd $B; rm -f DL.done
PYTHONUTF8=1 python3 -u $S/tk410/tk410-sqd-evm.py base-mainnet pools.csv base $B/sqd 1792022400 1792713599 >> $B/dl-base.log 2>&1
[ -f $B/sqd/base/DONE.json ] && touch $B/DL.done
