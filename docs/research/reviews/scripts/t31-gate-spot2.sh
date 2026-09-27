#!/usr/bin/env bash
# Судья 27.09: (д) на сутках, где у T-29 есть сделки; счётчики эталона 16–23 против 16–25.09.
cd ~/alpha
strip() { grep -av '^#' "$1" | awk -F, 'BEGIN{OFS=","} {$4=""; print}' | sort; }
n=0; eq=0; rows=0
for h in epochs/e-aug tmp-lsk0914.used-20260926/home tmp-t29/rec; do
  for b in "$h"/b5/t29-a60u100k/2026-*/t29-a60u100k/rounds.csv; do
    d=$(basename "$(dirname "$(dirname "$b")")"); a="$h/b5/t31-filt/$d/t-bid-btc4h-q1/rounds.csv"
    k=$(grep -avc '^#' "$b"); [ "$k" -le 1 ] && continue
    n=$((n+1)); rows=$((rows+k-1)); cmp -s <(strip "$a") <(strip "$b") && eq=$((eq+1)) || echo "РАЗНОЕ $h $d"
  done
done
echo "(д) суток со сделками T-29: $n, совпало $eq, сделок $rows"
for last in 23 25; do
  awk -F, -v last=$last 'FNR==1{next} /^#/{next} {split(FILENAME,p,"/"); d=substr(p[3],9,2)+0; if (d>=16 && d<=last) {s+=$4; f+=$6; b+=$7}} END{print "16–" last ".09: сигналов " s ", исполнений " f ", занято " b}' b5/titrc-u500r/2026-09-*/t-bid-btc4h-q1/forms.csv
done
