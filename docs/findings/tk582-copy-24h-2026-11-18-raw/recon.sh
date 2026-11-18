#!/bin/bash
# $1 = book|trade
cd /data/tk582v3/rep24
K=$1
LO=1794914041000; HI=1795000441000; P=/data/tk582v3/pool.json; R=/data/tk582v3/tk582_rec_recon.py
LIVE=$(ls /data/collect/binance/data/2026-11-1[78]/${K}*.tsv.zst | sort -t/ -k6,6 )
COPY=$(ls /data/tk582v3/data/2026-11-1[78]/${K}*.tsv.zst)
python3 $R dump --kind $K --pool $P --from-ms $LO --to-ms $HI --out rec-$K-live.pkl $LIVE 2> rec-$K-live.err
python3 $R dump --kind $K --pool $P --from-ms $LO --to-ms $HI --out rec-$K-copy.pkl $COPY 2> rec-$K-copy.err
python3 $R cmp --live rec-$K-live.pkl --copy rec-$K-copy.pkl --json rec-$K.json > rec-$K.md 2> rec-$K.cmp.err
touch rec-$K.done
