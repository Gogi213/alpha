#!/usr/bin/env bash
# TK-064 шаг 4: цена флага --r1-cols на одних сутках (off/on вперемешку), user/wall/RSS; под benchrun.sh stand.
#   tk064-cost.sh <бинарник> <каталог> <ЭПОХА>:<SYM>:<ДЕНЬ> ; REPS=3
set -uo pipefail
B=$1; OUT=$2; IFS=: read -r ep sym day <<<"$3"; REPS=${REPS:-3}
R="$OUT/root"; rm -rf "$R"; mkdir -p "$R"
for f in /data/alpha/epochs/$ep/root/*"$sym-$day"* /data/alpha/epochs/$ep/root/verify-"$sym".status; do ln -s "$f" "$R/"; done
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for i in $(seq "$REPS"); do for v in off on; do
  d="$OUT/$v-$i"; rm -rf "$d"; mkdir -p "$d/mf"; x=""; [ $v = on ] && x="--r1-cols"
  /usr/bin/time -f "$v user=%U wall=%e rss_kb=%M" -o "$d/time" nice -n 5 "$B" lob touches --root "$R" --symbol "$sym" $FL --minute-flow "$d/mf" --out "$d/touches-$sym.csv" $x > "$d/log" 2>&1
  cat "$d/time"
done; done
