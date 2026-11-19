#!/bin/bash
# отладка локального TLS-источника: один сервер, один коллектор 15 с, вывод в /data/perf/st/dbg.out
cd /data/perf/st
for u in $(systemctl list-units "st-*" --no-legend | awk '{print $1}'); do systemctl stop $u; done
for p in $(pgrep -f "tlsws_replay.py"); do [ $p != $$ ] && kill $p 2>/dev/null; done
sleep 1
rm -rf dbg-data
python3 /data/perf/tlsws_replay.py 19300 /data/perf/cap/binance/data/capture-book00.tsv srv.pem srv.key 20 > dbg2.txt 2>&1 &
sleep 4
ALPHA_TLS_EXTRA_ROOT=/data/perf/st/ca.pem timeout 25 /data/perf/alpha-new5 collect run --exchange binance --root /data/perf/st/dbg-data --health /data/perf/st/dbg.json --pool-file /data/collect/binance/data/pool-2026-11-13.json --only-shards book00 --lanes 2 --tls-ws localhost:19300 --connect-budget 20 --duration-s 15 --rx-stamp user > dbg.out 2>&1
cat dbg2.txt >> dbg.out
tail -3 /data/perf/st/dbg-data/events-book00.jsonl | cut -c1-200 >> dbg.out
echo END >> dbg.out
