#!/usr/bin/env bash
# TK-319: гейт раннера спота. Шаги на 1 монето-сутках, итог — /data/tk0319/gate/RESULT.txt.
#  A) фьючерс: day-cells-old.sh (HEAD до TK-319) против day-cells.sh на 1 символо-сутках B1 (1 клетка) → md5 out/ равны
#  B) спот: свечи ETH/BTC spot → σ240 → корень эпохи sep → касания D20 → режим BTC спот → day-cells S0 → rc=0, «символов N>0»
# Запуск: alsched submit --kind gate --cores 3 … -- bash /data/tk0319/bin/tk319-gate.sh
set -uo pipefail
K=/data/tk0319; B=$K/bin; G=${G:-$K/gate}; JG=${JG:-$K/jrn}; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}
SYM=${SYM:-ETHUSDT}; MON=sep; DAY=2026-09-05
mkdir -p "$G"; R=$G/RESULT.txt; : > "$R"
say() { echo "$*" | tee -a "$R"; }
# ---- A: фьючерс байт в байт
FP=$G/fplan; mkdir -p "$FP"
echo 'ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1' > "$FP/b1.cells"
printf '%s\n' '--deadline-secs 14400' '--entry-form ladder3x0..0.0409sw2' '--entry-ttl-secs 1800' '--stop-form pct2' '--take-form tr1x1' \
  '--set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55' > "$FP/b1.args"
for m in 01 02 03 04 05 06 07 08 09; do : > "$FP/b1-2026-$m.cells"; : > "$FP/b1-2026-$m.args"; done
printf 'month,day,symbol\njan,2026-01-02,ADAUSDT\n' > "$G/b1-one.csv"
for v in old new; do
  rm -rf "$G/fut-$v"
  scr=day-cells.sh; [ $v = old ] && scr=day-cells-old.sh
  PLAN=b1 P=$FP O=$G/fut-$v B1=$G/b1-one.csv BIN=$BIN GT=1 TP=1 bash "$B/$scr" x jan 2026-01-02 2>&1 | tail -3
done
( cd "$G/fut-old/x/out" && find . -type f | sort | xargs md5sum ) > "$G/fut-old.md5"
( cd "$G/fut-new/x/out" && find . -type f | sort | xargs md5sum ) > "$G/fut-new.md5"
if [ -s "$G/fut-old.md5" ] && cmp -s "$G/fut-old.md5" "$G/fut-new.md5"; then say "A фьючерс: байт в байт, файлов $(wc -l < "$G/fut-new.md5")"; else say "A фьючерс: РАСХОЖДЕНИЕ или пусто (old $(wc -l < "$G/fut-old.md5"), new $(wc -l < "$G/fut-new.md5"))"; fi
# ---- B: спот
python3 "$B/ref-klines.py" --category spot --out-dir "$K/klines" --symbols "$SYM,BTCUSDT,ETHUSDT" --since 2026-08-31 --until 2026-09-06 2>&1 | tail -3
python3 "$B/sigma-table.py" --klines "$K/klines" --out-dir "$K/sigma240" --symbols "$SYM" 2>&1 | tail -2
python3 "$B/tk319-spot-roots.py" epoch $MON $DAY | tail -1
K=$K JRN=$JG BIN=$BIN SYMS=$SYM bash "$B/tk319-jrn-day.sh" $MON $DAY; cat "$JG/$DAY/fail.txt"
RG=$K/regime/$MON; mkdir -p "$RG" "$K/jrn-view"; ln -sfn "$JG/$DAY/wt/sub/D20/$DAY" "$K/jrn-view/$DAY"
for s in BTCUSDT ETHUSDT; do ln -sf "$K/klines/ref-$s-1m.csv" "$RG/ref-$s-1m.csv"; done
python3 "$B/regime.py" --day $DAY --touches "$K/jrn-view" --regime-dir "$RG" 2>&1 | tail -2
SP=$G/splan; mkdir -p "$SP"; bash "$B/tk313-spot-plan.sh" "$SP/full" > /dev/null
cp "$SP/full/spot.args" "$SP/s0.args.all"; grep -v '^--set' "$SP/full/spot.args" > "$SP/spots0.args"; grep ' s0:' "$SP/full/spot.args" >> "$SP/spots0.args" || grep '^--set s0:' "$SP/full/spot.args" >> "$SP/spots0.args"
grep -E ' s0$' "$SP/full/spot.cells" > "$SP/spots0.cells"
for m in 01 02 03 04 05 06 07 08 09; do : > "$SP/spots0-2026-$m.cells"; : > "$SP/spots0-2026-$m.args"; done
rm -rf "$G/spot-s0"
PLAN=spots0 P=$SP O=$G/spot-s0 EPOCH=$K/e-$MON VERDICT=$K/verdict.csv B1=$K/b1-symdays.csv JRN=$JG RG=$K/regime BIN=$BIN GT=1 TP=1 \
  bash "$B/day-cells.sh" s0 $MON $DAY; rc=$?
say "B спот S0: rc=$rc fail=$(wc -c < "$G/spot-s0/s0/fail.txt")"
grep -h -i -E "символ" "$G/spot-s0/s0/log/"*.log | head -3 | tee -a "$R"
ls "$G/spot-s0/s0/out/$DAY" 2>/dev/null | head -5 | tee -a "$R"
touch "$G/done"
