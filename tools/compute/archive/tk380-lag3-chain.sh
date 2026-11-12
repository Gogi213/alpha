#!/bin/bash
cd /data/tk0380
while [ ! -f /data/tk0380/bbo/DONE.json ]; do sleep 30; done
python3 -u tk380-bsc-lag3.py /data/tk0380/bsclag3 > /data/tk0380/bsclag3.log 2>&1
python3 tk380-bsc-lag3-summ.py /data/tk0380/bsclag3 /data/tk0380/bsclag3/agg.json > /data/tk0380/bsclag3/agg.txt 2>&1
echo done > /data/tk0380/bsclag3/SUMM.done
