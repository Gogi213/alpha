#!/bin/bash
# TK-394 П-28 п.1: покрытие BBO Gate по 104 монетам (15 процессов). Итог /data/tk0394/cover/DONE.json
cd /data/tk0394
exec >>/data/tk0394/cover-run.log 2>&1
mkdir -p cover
cat common-fut.txt | xargs -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-cover.py /data/tk0394/cover {}
echo "{\"done\":true,\"n\":$(ls cover/*.json | grep -vc DONE)}" > cover/DONE.json
