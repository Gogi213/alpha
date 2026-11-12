#!/bin/bash
# TK-394 возврат 2: 6 монет-кандидатов (gate, 1 ядро). Итог /data/tk0394/cand/DONE.json
cd /data/tk0394
exec >>/data/tk0394/cand-run.log 2>&1
mkdir -p cand
echo ERA IOST DOOD BMT FLOCK WAL | tr ' ' '\n' | xargs -P1 -I{} /data/tk0380/npvenv/bin/python /data/tk0394/tk394-cand.py /data/tk0394/cand {}
echo "{\"done\":true,\"n\":$(ls cand/*.json | grep -vc DONE)}" > cand/DONE.json
