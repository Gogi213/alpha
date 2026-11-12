#!/bin/bash
# TK-394 шаг 1: замер расхождений по 104 общим монетам, 15 процессов. Итог /data/tk0394/spread2/DONE.json
cd /data/tk0394
exec >>/data/tk0394/spread2-run.log 2>&1
mkdir -p spread2
cat bbo-order.txt | xargs -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-spread.py /data/tk0394/spread2 {}
echo "{\"done\":true,\"n\":$(ls spread2/*.json | grep -vc DONE)}" > spread2/DONE.json
