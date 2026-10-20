#!/bin/bash
# TK-328, П-22 §6, гейт к Летописи: B1 фьюча (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 × t-bid-btc4h-q1) на 1 монето-сутках,
# сигналы подаются через `lob touches-project` (spot == fut: один набор бинлога) вместо готового approaches-<SYM>.csv.
# Ожидание: (1) approaches-<SYM>.csv после проекции == исходному байт в байт; (2) строки B1 этой монеты и суток в rounds.csv
# == строкам эталона B1 фьюча (дерево Летописи b1-b: /data/p12r3a/b1-b/<мес>/<сутки>/t-bid-btc4h-q1/rounds.csv, TK-191/TK-326) по
# первым 16 колонкам (в эталоне колонок на две меньше — R1-хвост появился позже).
# Запуск на calc под alsched (--kind gate): gate-b1.sh <бинарник> <OUT> <SYM> <YYYY-MM-DD> <мес jan|feb|…>
# Остальное — как tools/compute/day-cells.sh (те же флаги, план F1), кроме списков --exit-form: клетка B1 формы выхода не называет (--exit-form none), а chase*/tape* только в r2-бинарнике.
set -euo pipefail
BIN=$1 OUT=$2 SYM=$3 D=$4 MON=$5
P=/data/tk0196/plan RG=/data/tk0196/regime JRN=/data/p12r3a/jrn REF=/data/p12r3a/b1-b/2026-${D:5:2}/$D/t-bid-btc4h-q1/rounds.csv
H=/data/tk046/$MON/home E=$H/alpha/epochs/e-$MON ym=2026-$(case $MON in jan) echo 01;; feb) echo 02;; mar) echo 03;; apr) echo 04;; may) echo 05;; jun) echo 06;; jul) echo 07;; aug) echo 08;; sep) echo 09;; esac)
FORM=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 SET=t-bid-btc4h-q1
export HOME=$H
rm -rf "$OUT"; W=$OUT/w; mkdir -p "$W/study/approaches" "$W/wt/sub/D20/$D" "$OUT/log"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches|regime) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$D")" "$W/study/root-$D"; ln -s "$RG/$MON" "$W/study/regime"
awk -F, -v s="$SYM" -v d="$D" 'FNR==1||($1==s&&$2==d)' /data/tk0196/out/$D/verdict-b1.csv > "$OUT/verdict.csv"
echo "$FORM $SET" > "$OUT/cells.txt"
PARTS=(); for f in "$W/study/root-$D/$SYM-$D.binlog" "$W/study/root-$D/$SYM-$D.binlog.zst"; do [ -e "$f" ] && PARTS+=("$f"); done
SRC=$JRN/$D/wt/sub/D20/$D/approaches-$SYM.csv; DST=$W/wt/sub/D20/$D/approaches-$SYM.csv
"$BIN" lob touches-project --in "$SRC" --out "$DST" --spot "${PARTS[@]}" --fut "${PARTS[@]}" | tee "$OUT/project-report.txt"
cmp "$SRC" "$DST" && echo "GATE1 approaches-csv: байт в байт"
args=(); while read -r f v; do [ -n "$f" ] || continue; [ "$f" = --exit-form ] && continue; if [ "$f" = --set ]; then n=${v%%:*}; [ "$n" = "$SET" ] || continue; fi; args+=("$f" "$v"); done < <(sort -u "$P/f1.args" "$P/f1-$ym.args")
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads 3 --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
(cd "$W" && "$BIN" lob bounce-grid --verdict-csv "$OUT/verdict.csv" --root "study/root-$D" --touches-from wt/sub/D20 "${common[@]}" --r1-cols --exit-form none --early-exit-secs off "${args[@]}" --cells "$OUT/cells.txt" --out-dir "$OUT/grid") > "$OUT/log/grid.log" 2>&1
NEW=$(find "$OUT/grid" -name rounds.csv | head -1)
pick() { grep -v '^#' "$1" | awk -F, -v s="$SYM" -v d="$D" -v f="$FORM" '$1==s&&$2==d&&$3==f' | cut -d, -f1-16; }
pick "$REF" > "$OUT/ref-rows.csv"; pick "$NEW" > "$OUT/new-rows.csv"
echo "строк эталон/новое: $(wc -l < "$OUT/ref-rows.csv") / $(wc -l < "$OUT/new-rows.csv")"
cmp "$OUT/ref-rows.csv" "$OUT/new-rows.csv" && echo "GATE2 rounds B1 $SYM $D: байт в байт с Летописью (b1-b)"
awk -F, '{n++; s+=$10*$9} END{printf "Σ net_bps·qty=%.4f n=%d\n", s, n}' "$OUT/new-rows.csv"
