#!/bin/bash
# TK-394 П-28 шаг 2 (посекундная сетка): 39 монет с валидными окнами (win>=95%), 15 процессов. Итог /data/tk0394/step2/DONE.json
cd /data/tk0394
exec >>/data/tk0394/step2-run.log 2>&1
mkdir -p step2
while read c; do [ -s step2/$c.json ] || echo $c; done < valid39.txt | xargs -r -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-step2.py /data/tk0394/step2 {}
echo "{\"done\":true,\"n\":$(ls step2/*.json | wc -l)}" > step2/DONE.json
