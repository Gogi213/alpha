#!/bin/bash
# TK-394 возврат: отклонение от прошлой медианы, свежесть ног, эпизоды. 15 процессов. Итог /data/tk0394/spread3/DONE.json
cd /data/tk0394
exec >>/data/tk0394/spread3-run.log 2>&1
mkdir -p spread3
cat bbo-order.txt | xargs -P15 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-spread3.py /data/tk0394/spread3 {}
echo "{\"done\":true,\"n\":$(ls spread3/*.json | grep -vc DONE)}" > spread3/DONE.json
