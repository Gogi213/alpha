#!/bin/bash
# usage: ab.sh <t0> <t1>
/tmp/sv 'cd /opt/probe-sofia; tar cf - binance/data/stats-*.jsonl gate/data/stats-*.jsonl' | /tmp/sx 'rm -rf /tmp/sofia; mkdir -p /tmp/sofia; cd /tmp/sofia; tar xf -; mkdir -p binance gate; mv binance/data/* binance/ 2>/dev/null; mv gate/data/* gate/ 2>/dev/null'
/tmp/sx "python3 -W ignore /tmp/ab.py $1 $2"
