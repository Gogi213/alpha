#!/usr/bin/env bash
# TK-212 вариант tk196-f1-day.sh: ДВЕ пачки на сутки (std без --tape-log и tape с --tape-log 300 только клеткам tape*/cxl*; гейт 04-20: −16 % ЦП-с), выход слит merge-packs.py. Оригинал: TK-196 П-14 F1: сутки × символы B1 (b1-symdays.csv), ОДИН bounce-grid --cells на все новые клетки (общие + месячные), журнал касаний p12r3a/jrn.
#   tk196-f1-day.sh <метка> <мес> <сутки>   env: BIN (r2-сборка 912dab65) GT P=/data/tk0196/plan O=/data/tk0196/out RG=/data/tk0196/regime
# Клетки: $P/f1.cells + $P/f1-<ГГГГ-ММ>.cells; аргументы форм/наборов: объединение $P/f1.args и $P/f1-<ГГГГ-ММ>.args.
# study/regime — копия месяца с колонками btc_ret_6h…24h (tk196-regime-long.py), исходный режим не меняется.
set -uo pipefail
lab=$1; MON=$2; d=$3
BIN=${BIN:-/data/tk0190/alpha-tk190b-r2}; TP=${TP:-4}; GT=${GT:-2}; P=${P:-/data/tk0196/plan}; OUT=${O:-/data/tk0196/out}; RG=${RG:-/data/tk0196/regime}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=$OUT/$lab; V=/data/tk044/final3/verdict.csv; B1=${B1:-/data/tk0115/delta/b1-symdays.csv}
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/out"; : > "$O/fail.txt"
export HOME=$H BIN O TP
ym=2026-$(case $MON in jan) echo 01;; feb) echo 02;; mar) echo 03;; apr) echo 04;; may) echo 05;; jun) echo 06;; jul) echo 07;; aug) echo 08;; sep) echo 09;; esac)
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$B1" | sort -u)
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches" "$W/b5" "$W/bin"; JRN=${JRN:-/data/p12r3a/jrn}; [ -e "$JRN/$d/done" ] || { echo "$d НЕТ ТАЧЕЙ ЖУРНАЛА $JRN/$d" >> "$O/fail.txt"; exit 4; }; ln -s "$JRN/$d/wt" "$W/wt"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches|regime) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"
[ -e "$RG/$MON/$d.csv" ] || { echo "$d НЕТ РЕЖИМА $RG/$MON" >> "$O/fail.txt"; exit 4; }; ln -s "$RG/$MON" "$W/study/regime"
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
cat "$P/f1.cells" "$P/f1-$ym.cells" > "$O/cells.txt"
grep -E 'tape|cxl' "$O/cells.txt" > "$O/cells-tape.txt"; grep -vE 'tape|cxl' "$O/cells.txt" > "$O/cells-std.txt"
t1=$(date +%s)
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
for pk in std tape; do
  [ -s "$O/cells-$pk.txt" ] || continue
  args=(); while read -r f v; do [ -n "$f" ] || continue; if [ "$f" = --set ]; then n=${v%%:*}; awk -v n="$n" '$2==n{f=1} END{exit !f}' "$O/cells-$pk.txt" || continue; fi; args+=("$f" "$v"); done < <(sort -u "$P/f1.args" "$P/f1-$ym.args")
  tl=(); [ $pk = tape ] && tl=(--tape-log 300)
  (cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols --exit-form none --early-exit-secs off "${args[@]}"     "${tl[@]}" --cells "$O/cells-$pk.txt" --out-dir "b5/out-$d-$pk" > "$O/log/$d.grid-$pk.log" 2>&1) || echo "$d GRID_FAIL $pk" >> "$O/fail.txt"
done
t2=$(date +%s)
mkdir -p "$O/out/$d"; python3 "$(dirname "$0")/merge-packs.py" "$W/b5/out-$d-std" "$W/b5/out-$d-tape" "$O/out/$d" || echo "$d MERGE_FAIL" >> "$O/fail.txt"
echo "$d syms_b1=$(echo "$syms"|grep -c .) grid_s=$((t2-t1))" >> "$OUT/units.txt"
rm -rf "$W"; touch "$O/done"
