#!/usr/bin/env bash
# TK-410: событийный BBO СПОТА Gate 15–22.10 (книги /srv/alpha-sda/tk410-spot-ob, 5 монет с книгой: FONE GT LAPTOP Q SI) -> /data/tk0393/tk410/spotgev/<COIN>/<YYYYMMDDHH>.npy; единицы монета×сутки
#   alsched submit --kind gate --name tk410-spotgev --cores 15 --max-runtime 1h --est 10m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-spotgev.sh
set -uo pipefail
O=/data/tk0393/tk410; OB=/srv/alpha-sda/tk410-spot-ob/spot/orderbooks/202610; PY=/data/tk0380/npvenv/bin/python
rm -f $O/spotgev.done; mkdir -p $O/spotgev
for c in FONE GT LAPTOP Q SI; do for d in 15 16 17 18 19 20 21 22; do echo "$c $d"; done; done > $O/spotgev-units.txt
NX=${ALSCHED_CORES:-15}
xargs -a $O/spotgev-units.txt -P$NX -L1 sh -c 'fs=$(ls '$OB'/${0}_USDT-202610${1}*.csv.gz | sort); '$PY' '$O'/tk410-spot-bbo-ev.py '$O'/spotgev "$0" $fs >/dev/null'; rc=$?
echo "rc=$rc ед $(wc -l < $O/spotgev-units.txt) npy $(ls $O/spotgev/*/*.npy | wc -l) $(date +%T)" > $O/spotgev.done
