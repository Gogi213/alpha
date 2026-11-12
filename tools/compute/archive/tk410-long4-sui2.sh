#!/bin/bash
# TK-410 long4 sui2: свопы Sui SUI(0x51e8…) 10 сегментов и US(0x968f…) 4 сегмента до 03.10 20:00 UTC (выше — уже в long4-sui/sui/, 04.10–14.10) параллельно по контрольным точкам. Метка sui4.done.
#   alsched submit --kind net --name tk410-long4-sui2 --cores 2 --max-runtime 4h --est 60m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long4-sui2.sh
cd /data/tk0393/tk410 || exit 1; rm -f sui4.done
O=/data/tk0393/tk410/long4-sui-seg
python3 -u tk410-sui-seg.py $O 1788220800 1792022399 0x51e883ba7c0b566a26cbc8a94cd33eb0abd418a77cc1e60ad22fd9b1f29cd2ab:10 0x968f1f73b8e766e2ce0198c807d0c956aa384bfe64c98a18e56bfb970a13e7bb:4:1791057600 > $O.log 2>&1
echo "rc=$? $(cat $O/*.jsonl | wc -l) $(date +%T)" > sui4.done
