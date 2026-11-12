#!/bin/bash
# TK-410 Sui: 7 пулов (DEEP MAGMA MMT SUI TRUTH US WAL) — середина из Swap -> map2 (15-22.10) на 15 ядрах. Метка sui.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; mkdir -p mid-sui out-sui
grep -E ",sui," cover.csv | awk -F, '{print $1" "$4}' > sui-list.txt
cat sui-list.txt | xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c '
  /data/tk0380/npvenv/bin/python tk410-sui-mid.py "$0" "$1" mid-sui/sui__"$1".csv > out-sui/"$0".ext.log 2>&1 &&
  GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk410-map2.py "$0" mid-sui/sui__"$1".csv out-sui/"$0" > out-sui/"$0".log 2>&1'
echo rc=$? > sui.done
