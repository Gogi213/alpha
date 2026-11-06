#!/usr/bin/env bash
# TK-493 F8 (S2, П-37 r3.2 §11.8): контроль «все пары каталога без отбора». Запуск - только по команде CEO; cd ~/xdl9/sel/waves; THREADS=8 bash wave4.sh
set -u
THREADS=${THREADS:-8}
mkdir -p out/w4 w4
python3 $HOME/xdl9/tools/statarb/xdl9/sel_allpairs_jobs.py binance-crypto w4/ALLPAIRS_A1_binance_crypto__binance__binance.jobs.csv
$HOME/xdl9/bin/statarb-xdl9-a24f4c96-pgo statarb run --kind pair --panels $HOME/xdl9/trade/binance --funding-y $HOME/xdl9/funding/binance --rearm --cells w2/A1_binance_crypto.cells.csv --jobs w4/ALLPAIRS_A1_binance_crypto__binance__binance.jobs.csv --daily-out out/w4/ALLPAIRS_A1_binance_crypto__binance__binance.daily.csv --cells-out out/w4/ALLPAIRS_A1_binance_crypto__binance__binance.cells_out.csv --ext-out out/w4/ALLPAIRS_A1_binance_crypto__binance__binance.ext.csv --mtm-open --min-span 1440 --threads ${THREADS:-8}
python3 $HOME/xdl9/tools/statarb/xdl9/sel_allpairs_jobs.py bingx-crypto w4/ALLPAIRS_A1_bingx_crypto__bingx__bingx.jobs.csv
$HOME/xdl9/bin/statarb-xdl9-a24f4c96-pgo statarb run --kind pair --panels $HOME/xdl9/trade/bingx --funding-y $HOME/xdl9/funding/bingx --rearm --cells w2/A1_bingx_crypto.cells.csv --jobs w4/ALLPAIRS_A1_bingx_crypto__bingx__bingx.jobs.csv --daily-out out/w4/ALLPAIRS_A1_bingx_crypto__bingx__bingx.daily.csv --cells-out out/w4/ALLPAIRS_A1_bingx_crypto__bingx__bingx.cells_out.csv --ext-out out/w4/ALLPAIRS_A1_bingx_crypto__bingx__bingx.ext.csv --mtm-open --min-span 1440 --threads ${THREADS:-8}
python3 $HOME/xdl9/tools/statarb/xdl9/sel_allpairs_jobs.py bingx-eq_us w4/ALLPAIRS_A1_bingx_eq_us__bingx__bingx.jobs.csv
$HOME/xdl9/bin/statarb-xdl9-a24f4c96-pgo statarb run --kind pair --panels $HOME/xdl9/trade/bingx --funding-y $HOME/xdl9/funding/bingx --rearm --cells w2/A1_bingx_eq_us.cells.csv --jobs w4/ALLPAIRS_A1_bingx_eq_us__bingx__bingx.jobs.csv --daily-out out/w4/ALLPAIRS_A1_bingx_eq_us__bingx__bingx.daily.csv --cells-out out/w4/ALLPAIRS_A1_bingx_eq_us__bingx__bingx.cells_out.csv --ext-out out/w4/ALLPAIRS_A1_bingx_eq_us__bingx__bingx.ext.csv --mtm-open --min-span 1440 --threads ${THREADS:-8}
