#!/bin/bash
# TK-394 П-28 шаг 2f (шаг 2 + фандинг, опора fund): 39 монет, 15 процессов. Итог /data/tk0394/step2f/DONE.json
cd /data/tk0394
exec >>/data/tk0394/step2f-run.log 2>&1
mkdir -p step2f
(echo ETH; grep -vx ETH valid39.txt) | while read c; do [ -s step2f/$c.json ] || echo $c; done | xargs -r -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-step2f.py /data/tk0394/step2f {}
echo "{\"done\":true,\"n\":$(ls step2f/*.json | wc -l)}" > step2f/DONE.json
