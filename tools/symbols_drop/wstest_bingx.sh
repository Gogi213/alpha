#!/bin/bash
set -e
R=/data/tk584/wstest-bingx; rm -rf $R; mkdir -p $R
python3 - <<'PY'
import json
rem=[l.strip() for l in open("/data/tk584/nc-rem25.txt") if l.strip()]
pool=rem[:40]+["BTC-USDT"]
json.dump({"pool":pool},open("/data/tk584/wstest-bingx-pool.json","w"))
PY
/data/collect/alpha-collect-bingx collect run --exchange bingx --root $R/data --health $R/health.json --lanes 1 --kinds book --pool-file /data/tk584/wstest-bingx-pool.json --duration-s 25 --reactor --worker-inline --reactor-window-us 10000 > $R/out.log 2>&1 || true
tail -3 $R/out.log
ls $R/data | head
for f in $R/data/*/book*.tsv.zst; do zstd -dc $f 2>/dev/null | awk -F'\t' '$1=="b"{n[$5]++} END{for(s in n) print s, n[s]}'; done | sort | awk '{c[$1]+=$2} END{for(s in c) print s, c[s]}' | sort -k2 -n | awk 'NR<=5||/BTC/{print}'
