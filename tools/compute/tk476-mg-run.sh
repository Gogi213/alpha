#!/usr/bin/env bash
# TK-476: слияние стадии с лежащими, N процессов по срезам (--part). usage: tk476-mg-run.sh <биржа> <N> <метка>
# Итог: /data/tk0476/<метка>.done (все срезы ok) или .fail; общая таблица сверки — candles-1m-476/overlap-<биржа>.csv.
cd /data/tk0476 || exit 1
ex=$1; n=$2; tag=$3
rm -f "$tag.done" "$tag.fail"
pids=()
for ((i = 0; i < n; i++)); do
  python3 tk476_merge.py "$ex" --apply --part "$i/$n" > "$tag.p$i.log" 2>&1 &
  pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
S=/srv/alpha-sda/candles-1m-476
if [ $rc = 0 ]; then
  { head -1 "$S/overlap-$ex.p0.csv"; for ((i = 0; i < n; i++)); do tail -n +2 "$S/overlap-$ex.p$i.csv"; done; } > "$S/overlap-$ex.csv"
  echo "ok $(date -u +%F\ %T) UTC" > "$tag.done"
else
  echo "fail $(date -u +%F\ %T) UTC" > "$tag.fail"
fi
