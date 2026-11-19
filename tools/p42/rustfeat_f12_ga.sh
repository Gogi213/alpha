#!/usr/bin/env bash
# TK-611 ворота f12: Rust (`alpha-feat f12`) против `eps-*.parquet` TK-599 (окна wa/wb/wc × потоки), побитово. srv; BIN — бинарь, OUT — каталог результатов.
#   BIN=/data/p42-tk611/bin/alpha-feat-f12 OUT=/data/p42-tk611/ga PY=/data/p42-tk611/py [WINS="wa wb wc"] [PROCS=3] [BIN_ARGS=…] bash rustfeat_f12_ga.sh
# Один запуск на таблицу: sigsx = /data/p42-tk599/sx/<окно>/<vid>/<long|short>-sigsx.csv, эталон = ftab/<окно>/<vid>/eps-<L|S>.parquet, кэш/сделки окна.
set -uo pipefail
BIN=${BIN:?BIN}; OUT=${OUT:-/data/p42-tk611/ga}; PY=${PY:-/data/p42-tk611/py}; WINS=${WINS:-"wa wb wc"}; PROCS=${PROCS:-3}; BIN_ARGS=${BIN_ARGS:-}
declare -A BD=([wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01)
mkdir -p "$OUT"; rc=0; n=0; ok=0
MD5=$(md5sum "$BIN" | cut -d" " -f1); echo "F12-GATE bin=$BIN md5=$MD5 args=[$BIN_ARGS] wins=[$WINS]"; echo "$MD5" > "$OUT/md5"
for w in $WINS; do
  for d in /data/p42-tk599/ftab/$w/f*/; do
    vid=$(basename "$d")
    for sd in L:long S:short; do
      L=${sd%%:*}; name=${sd##*:}; ref=$d/eps-$L.parquet; sx=/data/p42-tk599/sx/$w/$vid/$name-sigsx.csv
      [ -f "$ref" ] && [ -f "$sx" ] || continue
      n=$((n+1)); tag=$w-$vid-$L
      nice -n 19 "$BIN" f12 --sigsx "$sx" --side "$name" --out "$OUT/$tag.csv" --cache /data/p42-tk599/$w/cache --trades /data/p42-tk599/$w/aggtrades \
        --base-date "${BD[$w]}" --threads "$PROCS" $BIN_ARGS > "$OUT/$tag.log" 2>&1 || { echo "$tag: rust rc!=0"; rc=1; continue; }
      if python3 "$PY/rustfeat_f12_gate.py" compare "$ref" "$OUT/$tag.csv" --out "$OUT/$tag.json" > "$OUT/$tag.gate" 2>&1; then ok=$((ok+1)); r=PASS; else rc=1; r=FAIL; fi
      echo "$tag $r md5=${MD5:0:8} $(tail -1 "$OUT/$tag.gate") | $(tail -1 "$OUT/$tag.log" | cut -c1-200)"
    done
  done
done
echo "F12-GATE md5=$MD5 tables=$n pass=$ok rc=$rc"; echo $rc > "$OUT/rc"
