#!/bin/bash
# TK-394: исправленный BBO Gate фьюча (tk394-gate-bbo.py) по 104 общим монетам, 15–22.10; 15 процессов, крупные монеты первыми. Итог /data/tk0394/bbo/DONE.json
cd /data/tk0394
exec >>/data/tk0394/bbo-run.log 2>&1
mkdir -p bbo
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610
for c in $(cat common-fut.txt); do echo "$(du -cb $OB/${c}_USDT-*.csv.gz 2>/dev/null | tail -1 | cut -f1) $c"; done | sort -rn | awk '{print $2}' > bbo-order.txt
cat bbo-order.txt | xargs -P15 -I{} python3 /data/tk0394/tk394-gate-bbo.py /data/tk0394/bbo {}
echo "{\"done\":true,\"n\":$(ls bbo/*.csv.gz | wc -l)}" > bbo/DONE.json
