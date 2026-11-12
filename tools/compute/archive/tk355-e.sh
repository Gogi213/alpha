#!/usr/bin/env bash
# TK-355 (G-015 E/L/Z): клетка B1 фьюч (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 × t-bid-btc4h-q1), по одному проходу на конфиг
#   «очередь × RTT × размер» (одна ручка за раз от боевой prob:3 / В-68 / $500), 251 сутки (3533 символо-суток), боевой PGO.
#   tk355-e.sh probe — 1 сутки × все конфиги (1 ядро); tk355-e.sh full — все сутки (env NX=15). Повтор — дельта (done-метки).
#   Выход /data/tk0355/e/<конфиг>/out/run-<мес>-<сутки>/ ; конфиги: см. CFG ниже (имя qm rtt_ns usd; rtt - = боевая тройка В-68).
set -uo pipefail
MODE=${1:-probe}
export REASON="TK-355 G-015 E/L/Z, B1 fut x 10 конфигов, pgo" BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo} SCRIPTS=/data/tk0355/scripts NX=${NX:-15} GT=${GT:-1} PLAN=L TP=1
R=/data/tk0355/e; export R; mkdir -p "$R"
CFG="base prob:3 - 500
qrisk risk-adverse - 500
rtt0 prob:3 0 500
rtt1ms prob:3 1000000 500
rtt10ms prob:3 10000000 500
rtt100ms prob:3 100000000 500
rtt1000ms prob:3 1000000000 500
usd250 prob:3 - 250
usd5000 prob:3 - 5000
usd50000 prob:3 - 50000"
echo "$CFG" > "$R/configs.txt"
CELL='ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1'
ARGS='--entry-form ladder3x0..0.0409sw2
--entry-ttl-secs 1800
--stop-form pct2
--take-form tr1x1
--deadline-secs 14400
--set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55'
B1=/data/tk0115/delta/b1-symdays.csv
while read -r c qm rtt usd; do
  P=$R/$c/plan; rm -rf "$P"; mkdir -p "$P" "$R/$c/out"
  echo "$CELL" > "$P/L.cells"; echo "$ARGS" > "$P/L.args"
  for m in 01 02 03 04 05 06 07 08 09; do : > "$P/L-2026-$m.cells"; : > "$P/L-2026-$m.args"; done
  for d in "$R/$c"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
done <<< "$CFG"
# сутки: тяжёлые первыми (число символов), probe — одни средние сутки
awk -F, 'NR>1{n[$1" "$2]++} END{for(k in n) print n[k], k}' "$B1" | sort -k1,1nr -k3,3 | awk '{print $2, $3}' > "$R/days.txt"
if [ "$MODE" = probe ]; then sed -n '120p' "$R/days.txt" > "$R/days-use.txt"; else cp "$R/days.txt" "$R/days-use.txt"; fi
while read -r m d; do while read -r c qm rtt usd; do echo "$c $m $d"; done <<< "$CFG"; done < "$R/days-use.txt" > "$R/units.txt"
export BIN SCRIPTS GT REASON PLAN TP
( while sleep 5; do ps -eo rss,args | awk '/lob bounce-grid/&&index($0,"tk0355/e/")&&!/awk/{s+=$1} END{print s/1048576}' >> "$R/rss-gb.log"; done ) & SAMP=$!   # пик суммарной RSS проходов (ГБ), для --mem полного счёта
xargs -P "$NX" -L1 bash -c 'c=$1; cfg=$(awk -v c="$c" "\$1==c" "$R/configs.txt"); set -- $cfg $2 $3; qm=$2; rtt=$3; usd=$4; m=$5; d=$6; if [ "$rtt" = - ]; then unset RTTM RTTP; else export RTTM=$rtt RTTP=$rtt; fi; QM=$qm USD=$usd P=$R/$c/plan O=$R/$c/out bash "$SCRIPTS/day-cells.sh" "run-$m-$d" "$m" "$d"' _ < "$R/units.txt"
rc=$?
kill $SAMP 2>/dev/null; echo "пик RSS проходов, ГБ: $(sort -g "$R/rss-gb.log" | tail -1)"
n=$(cat "$R"/*/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
