#!/bin/bash
# tk046-klines.sh: свечи 1m (с 2026-07-31 по 2026-08-31, как у прежних монет e-aug) и sigma240 для новых монет августа
O=/data/tk046/aug/study; mkdir -p $O/klines $O/sigma240; t0=$(date +%s)
xargs -a /data/tk046/aug-newcoins.txt -P 8 -I{} python3 /opt/alpha-compute/bin/ref-klines.py --out-dir $O/klines --symbols {} --since 2026-07-31 --until 2026-08-31 > $O/klines.log 2>&1
echo "klines_wall_s=$(( $(date +%s) - t0 ))" > $O/klines_done
python3 /opt/alpha-compute/bin/sigma-table.py --klines $O/klines --out-dir $O/sigma240 --symbols $(paste -sd, /data/tk046/aug-newcoins.txt) > $O/sigma.log 2>&1
echo "sigma_rc=$? total_wall_s=$(( $(date +%s) - t0 ))" >> $O/klines_done
