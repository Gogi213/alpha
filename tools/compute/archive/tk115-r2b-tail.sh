#!/usr/bin/env bash
# TK-115 R2-B хвост: 2026-08-31 и 2026-09-14 упали GRID_FAIL (wall2 ушли сольным путём у ADAUSDT 08-31 / STXUSDT 09-14);
# досчёт этих суток без упавшего символа (B1 урезан на 2 символо-сутки), остальные символы суток — как в основном проходе.
set -uo pipefail
B1=/data/tk0115/delta/b1-symdays.csv; B1X=/data/tk0115/delta/b1-symdays-r2b-tail.csv
awk -F, '!(($2=="2026-08-31"&&$3=="ADAUSDT")||($2=="2026-09-14"&&$3=="STXUSDT"))' "$B1" > "$B1X"
rm -rf /data/p12r3a/r2b/2026-08-31/claim /data/p12r3a/r2b/2026-08-31/done /data/p12r3a/r2b/2026-08-31/out /data/p12r3a/r2b/2026-08-31/w-2026-08-31
rm -rf /data/p12r3a/r2b/2026-09-14/claim /data/p12r3a/r2b/2026-09-14/done /data/p12r3a/r2b/2026-09-14/out /data/p12r3a/r2b/2026-09-14/w-2026-09-14
B1=$B1X bash /data/tk0115/tk115-r2b-day.sh 2026-08-31 aug 2026-08-31
B1=$B1X bash /data/tk0115/tk115-r2b-day.sh 2026-09-14 sep 2026-09-14
cat /data/p12r3a/r2b/2026-08-31/fail.txt /data/p12r3a/r2b/2026-09-14/fail.txt
