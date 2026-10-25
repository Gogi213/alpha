#!/bin/bash
# TK-394 holes2: дыры книги/ленты + cover по валидным окнам, 104 монеты (15 процессов, тяжёлые первыми; готовые json и Q пропуск — Q отдельной заявкой). Итог holes2/DONE.json
cd /data/tk0394
exec >>/data/tk0394/holes2-run.log 2>&1
mkdir -p holes2
grep -vx Q bbo-order.txt | while read c; do [ -s holes2/$c.json ] || echo $c; done | xargs -r -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-holes2.py /data/tk0394/holes2 {}
echo "{\"done\":true,\"n\":$(ls holes2/*.json | grep -vc DONE)}" > holes2/DONE.json
