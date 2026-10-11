#!/bin/bash
# G3 signals (TK-064): 1 сутки (SIG_DAY, умолч. 2026-01-01) × NS монет; <HEAD> без флага, <CAND> без флага и с --r1-cols; signals*.csv: cand off == head побайтно, cand on — 11 старых колонок побайтно.
#   tk064-sig.sh <HEAD бинарь> <CAND бинарь> <OUT> ; запуск через benchrun.sh stand
set -uo pipefail
HB=/opt/alpha-compute/bin/$1; CB=/opt/alpha-compute/bin/$2; O=$3; d=${SIG_DAY:-2026-01-01}; NS=${NS:-8}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; V=/data/tk044/final3/verdict.csv
export HOME=$H; rm -rf "$O"; mkdir -p "$O"
syms=${SYMS:-$(awk -F, -v d="$d" 'NR>1 && $2==d && $17=="пускаем"{print $1}' "$V" | sort -u | head -n "$NS")}
mk() { # $1 метка
  local W=$O/w-$1; mkdir -p "$W/study/approaches/D20/$d" "$W/b5"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
  mkdir -p "$W/study/root-$d"; cp -rs "$(readlink -f "$E/study/root-$d")"/. "$W/study/root-$d"/
}
touches() { # $1 дерево $2 бинарь $3 доп
  for s in $syms; do nice -n 5 "$2" lob touches --root "$1/study/root-$d" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 $3 --out "$1/study/approaches/D20/$d/touches-$s.csv" > "$O/touches-$s.log" 2>&1 || echo "TOUCHES_FAIL $s"; done
}
grid() { # $1 дерево $2 бинарь $3 доп
  (cd "$1" && nice -n 5 "$2" lob bounce-grid --verdict-csv "$V" --root "study/root-$d" --touches-from study/approaches/D20 --signal approach --queue-model prob:3 \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off \
    --threads 2 --hold-step skip --exit-group on --events wide --entry-form ladder3x0..0.0409sw2 --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 \
    --sigma-from study/sigma240 --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 $3 --out-dir b5/out > "$1/grid.log" 2>&1) || echo "GRID_FAIL $1"
}
mk h; mk c; mk r
touches $O/w-h "$HB" ""; grid $O/w-h "$HB" ""
cp -a $O/w-h/study/approaches/D20 $O/w-c/study/approaches/; rm -rf $O/w-c/study/approaches/D20/*/.keep 2>/dev/null
grid $O/w-c "$CB" ""
touches $O/w-r "$CB" "--r1-cols"; grid $O/w-r "$CB" "--r1-cols"
n=0; bad=0
for f in $(cd $O/w-h/b5/out && find . -type f \( -name 'signals*.csv' -o -name 'rounds*.csv' \) | sort); do n=$((n+1))
  cmp -s $O/w-h/b5/out/$f $O/w-c/b5/out/$f || { echo "DIFF off $f"; bad=1; }
  cmp -s <(cut -d, -f1-11 $O/w-h/b5/out/$f) <(cut -d, -f1-11 $O/w-r/b5/out/$f) || { echo "DIFF on-11col $f"; bad=1; }
done
rows=$(cat $(find $O/w-h/b5/out -name 'signals*.csv') 2>/dev/null | wc -l)
echo "signals_files $n head_lines $rows on_cols $(awk -F, 'NR==2{print NF}' $(find $O/w-r/b5/out -name 'signals*.csv' | head -1)) bad=$bad"
[ $n -gt 0 ] && [ "$rows" -gt 10 ] && [ $bad = 0 ] && echo "SIG OK" || echo "SIG FAIL"
