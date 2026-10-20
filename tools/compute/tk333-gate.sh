#!/bin/bash
# TK-333 (П-22 §5, контроль C): гейт и [вживую] на 1 монето-сутках (ADA 2026-01-15, спот → фьюч).
#   tk333-gate.sh <старый бинарник (touches-project без сдвига)> <новый> <OUT>
# G1: touches-project --shift-secs 0 == старый (cmp, спот ≠ фьюч). G2: bounce-grid без флага: новый == старый на той же проекции.
# SETX=<имя:оси> подменяет набор B1 (имя то же) — мягкий порог btc4h, чтобы L2 показал разницу.
# L1: сдвиги ±3600/±7200 — строк, arm_ms−ctx_ms == N у всех, ctx_ms ⊂ исходных arm_ms. L2: bounce-grid --ctx-shift-secs 3600 идёт и отличается от 0.
set -euo pipefail
OLD=$1 NEW=$2 O=$3 D=2026-01-15 S=ADAUSDT MON=jan
P=/data/tk0196/plan RG=/data/tk0196/regime
H=/data/tk046/$MON/home E=$H/alpha/epochs/e-$MON ym=2026-01 SET=t-bid-btc4h-q1 FORM=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
SRC=/data/tk0319/jrn/$D/wt/sub/D20/$D/approaches-$S.csv
export HOME=$H
rm -rf "$O"; mkdir -p "$O"
SP=(); FP=()
for f in /data/tk0319/e-jan/study/root-$D/$S-$D.binlog /data/tk0319/e-jan/study/root-$D/$S-$D.binlog.zst; do [ -e "$f" ] && SP+=("$f"); done
for f in "$E/study/root-$D/$S-$D.binlog" "$E/study/root-$D/$S-$D.binlog.zst"; do [ -e "$f" ] && FP+=("$f"); done
echo "spot=${SP[*]} fut=${FP[*]}"
"$OLD" lob touches-project --in "$SRC" --out "$O/old0.csv" --spot "${SP[@]}" --fut "${FP[@]}" > "$O/old0.rep"
"$NEW" lob touches-project --in "$SRC" --out "$O/new0.csv" --spot "${SP[@]}" --fut "${FP[@]}" --shift-secs 0 > "$O/new0.rep"
cmp "$O/old0.csv" "$O/new0.csv" && echo "G1 touches-project --shift-secs 0 == старый: байт в байт ($(wc -l < "$O/new0.csv") строк)"
diff "$O/old0.rep" "$O/new0.rep" && echo "G1 отчёт равен"
for N in 3600 -3600 7200 -7200; do
  "$NEW" lob touches-project --in "$SRC" --out "$O/s$N.csv" --spot "${SP[@]}" --fut "${FP[@]}" --shift-secs "$N" > "$O/s$N.rep"
  awk -F, -v n="$N" 'FILENAME==ARGV[1]{if(FNR==1){for(i=1;i<=NF;i++)if($i=="arm_ms")a0=i}else o[$a0]=1; next}
    FNR==1{for(i=1;i<=NF;i++){if($i=="arm_ms")a=i; if($i=="ctx_ms")c=i}; next}
    {k++; if(($a-$c)/1000!=n)bad++; if(!($c in o))miss++} END{printf "L1 shift %s: строк %d, arm_ms-ctx_ms!=N: %d, ctx_ms вне исходных arm_ms: %d\n",n,k,bad+0,miss+0}' "$SRC" "$O/s$N.csv"
  head -c 300 "$O/s$N.rep"; echo
done
grid() { # grid <бинарник> <тег> <approaches.csv> [доп. флаги bounce-grid]
  local bin=$1 tag=$2 csv=$3; shift 3; local W=$O/w-$tag
  mkdir -p "$W/study/approaches" "$W/wt/sub/D20/$D" "$O/log"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches|regime) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
  ln -s "$(readlink -f "$E/study/root-$D")" "$W/study/root-$D"; ln -s "$RG/$MON" "$W/study/regime"
  cp "$csv" "$W/wt/sub/D20/$D/approaches-$S.csv"
  awk -F, -v s="$S" -v d="$D" 'FNR==1||($1==s&&$2==d)' /data/tk0196/out/$D/verdict-b1.csv > "$O/verdict.csv"
  echo "$FORM $SET" > "$O/cells.txt"
  local args=(); while read -r f v; do [ -n "$f" ] || continue; [ "$f" = --exit-form ] && continue; if [ "$f" = --set ]; then n=${v%%:*}; [ "$n" = "$SET" ] || continue; [ -n "${SETX:-}" ] && v=$SETX; fi; args+=("$f" "$v"); done < <(sort -u "$P/f1.args" "$P/f1-$ym.args")
  local common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads 1 --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
  (cd "$W" && "$bin" lob bounce-grid --verdict-csv "$O/verdict.csv" --root "study/root-$D" --touches-from wt/sub/D20 "${common[@]}" --r1-cols --exit-form none --early-exit-secs off "${args[@]}" "$@" --cells "$O/cells.txt" --out-dir "$O/grid-$tag") > "$O/log/grid-$tag.log" 2>&1 || { echo "GRID FAIL $tag"; tail -5 "$O/log/grid-$tag.log"; }
}
grid "$OLD" old0 "$O/old0.csv"
grid "$NEW" new0 "$O/new0.csv"
diff -r "$O/grid-old0/$SET" "$O/grid-new0/$SET" && echo "G2 bounce-grid без флага: новый == старый на проекции ($(wc -l < "$O/grid-new0/$SET/rounds.csv") строк rounds)"
grid "$NEW" s3600 "$O/s3600.csv" --ctx-shift-secs 3600
grid "$NEW" s3600nc "$O/s3600.csv"
echo "L2 rounds: shift0=$(wc -l < "$O/grid-new0/$SET/rounds.csv") s3600(ctx)=$(wc -l < "$O/grid-s3600/$SET/rounds.csv") s3600(без ctx)=$(wc -l < "$O/grid-s3600nc/$SET/rounds.csv")"
cmp -s "$O/grid-s3600/$SET/rounds.csv" "$O/grid-s3600nc/$SET/rounds.csv" && echo "L2 ctx-shift НЕ меняет rounds (проверить!)" || echo "L2 ctx-shift меняет rounds (σ/режим из t)"
for N in 3600 -3600 7200 -7200; do
  grid "$NEW" c$N "$O/new0.csv" --ctx-shift-secs "$N"
  echo "L3 ctx-shift $N на сигналах без сдвига: кругов $(grep -vc '^#\|^symbol' "$O/grid-c$N/$SET/rounds.csv") (без флага $(grep -vc '^#\|^symbol' "$O/grid-new0/$SET/rounds.csv")); строки: $(grep -v '^#\|^symbol' "$O/grid-c$N/$SET/rounds.csv" | cut -d, -f4-10 | head -3 | tr '
' '|')"
done
echo DONE
