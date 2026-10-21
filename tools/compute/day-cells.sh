#!/usr/bin/env bash
# TK-219 (В-229 п.8–9): ЕДИНЫЙ раннер «сутки × клетки» (шаблон; tk1xx-*-day.sh — копии, в archive). Из tk212-f1-day2.sh.
#   day-cells.sh <метка> <мес> <сутки>   env: BIN (pgo/r2-pgo из MANIFEST, иначе alsched откажет проход > 15 мин) GT P O RG B1 JRN
#   Спот (TK-319): EPOCH (дерево эпохи: root/, study/{sigma240,root-<сутки>}), VERDICT (вердикт-csv) — без них корни фьючерса.
# Две пачки на сутки: std без --tape-log и tape с --tape-log 300 только клеткам tape*/cxl* (флаги журнала — по плану клеток $P/<PLAN>.cells).
# Порядок суток «тяжёлые первыми» — day-order.sh <b1-symdays.csv> <мес> (по числу символов, убывание): подавать в xargs/цикл в этом порядке.
# План: $P/$PLAN.cells + $P/$PLAN-<ГГГГ-ММ>.cells, аргументы форм — объединение $P/$PLAN.args и $P/$PLAN-<ГГГГ-ММ>.args (PLAN по умолчанию f1).
# σ-окно (TK-289 ось 8): env SIG=sigma<мин> SIGROOT=<корень таблиц окна: <мес>/sigma-<SYM>.csv> — без них study/sigma240 эпохи.
set -uo pipefail
PLAN=${PLAN:-f1}
lab=$1; MON=$2; d=$3
BIN=${BIN:?BIN обязателен: pgo/r2-pgo бинарь из MANIFEST (build-pgo.sh)}; TP=${TP:-4}; GT=${GT:-2}; P=${P:-/data/tk0196/plan}; OUT=${O:-/data/tk0196/out}; RG=${RG:-/data/tk0196/regime}
H=/data/tk046/$MON/home; E=${EPOCH:-$H/alpha/epochs/e-$MON}; O=$OUT/$lab; V=${VERDICT:-/data/tk044/final3/verdict.csv}; B1=${B1:-/data/tk0115/delta/b1-symdays.csv}
MF=$(dirname "$0")/manifest.py; [ -f "$MF" ] || MF=/data/sched/manifest.py; python3 "$MF" fastbin "$BIN" >&2 || { echo "day-cells: $BIN не на быстром пути (pgo/r2-pgo); обход — REASON=причина" >&2; exit 3; }   # В-229 п.8
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/out"; : > "$O/fail.txt"
export HOME=$H BIN O TP
ym=2026-$(case $MON in jan) echo 01;; feb) echo 02;; mar) echo 03;; apr) echo 04;; may) echo 05;; jun) echo 06;; jul) echo 07;; aug) echo 08;; sep) echo 09;; esac)
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$B1" | sort -u)
# copy-fresh:begin (TK-264) копия суток на sda годится, только если исходник на sdb с тех пор не менялся; иначе — исходник + сигнал
stale() { local m; m="$(date +%FT%T) КОПИЯ $d УСТАРЕЛА ($1): читаю исходник на sdb, копию обновить или убрать из ${ALT_ROOT-/srv/alpha-sda/root}"; echo "$m" >&2; echo "$m" >> "$O/stale-copy.txt"; { echo "$m" >> "${ALERTS-/data/sched/alerts.log}"; } 2>/dev/null; return 0; }
sig() { (cd "$1" && stat -L -c '%n %s %Y' * .[!.]* 2>/dev/null | LC_ALL=C sort); }
jrn_fresh() { [ ! "$1/$d/done" -nt "$2/$d/done" ] && [ "$(stat -c %y "$1/$d/wt")" = "$(stat -c %y "$2/$d/wt")" ]; }
root_fresh() { [ ! -d "$1" ] || [ "$(sig "$1")" = "$(sig "$2")" ]; }
# copy-fresh:end
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches" "$W/b5" "$W/bin"; JRN=${JRN:-/data/p12r3a/jrn}; AJ=${ALT_JRN-/srv/alpha-sda/jrn}; if [ -e "$AJ/$d/done" ]; then if [ ! -e "$JRN/$d/done" ] || jrn_fresh "$JRN" "$AJ"; then JRN=$AJ; else stale "журнал тачей: done/wt на sdb новее копии"; fi; fi; [ -e "$JRN/$d/done" ] || { echo "$d НЕТ ТАЧЕЙ ЖУРНАЛА $JRN/$d" >> "$O/fail.txt"; exit 4; }; [ -n "${SPOT_EPOCH:-}" ] || ln -s "$JRN/$d/wt" "$W/wt"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches|regime) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
RS=$(readlink -f "$E/study/root-$d"); AR=${ALT_ROOT-/srv/alpha-sda/root}/root-$d; if [ -d "$AR" ] && ! root_fresh "$RS" "$AR"; then stale "root-$d: размеры/время файлов не совпали с sdb"; AR=; fi
ln -s "$( [ -d "$AR" ] && echo "$AR" || echo "$RS")" "$W/study/root-$d"
[ -z "${SIGROOT:-}" ] || { rm -f "$W/study/${SIG:-sigma240}"; ln -s "$SIGROOT/$MON" "$W/study/${SIG:-sigma240}"; }   # TK-289 ось 8: окно σ; без env — study/sigma240 эпохи как прежде
# TK-332 (П-22 B/C): SPOT_EPOCH = дерево эпохи спота (study/root-<сутки>), JRN = журнал касаний СПОТА (при нём env ALT_JRN= пустой): подходы спота
# проецируются `lob touches-project` (как gate-b1.sh) в тик фьючерса, бэктест — на корне фьючерса. Монеты без входа проекции — в fail.txt.
if [ -n "${SPOT_EPOCH:-}" ]; then
  shf=(); [ "${SHIFT:-0}" != 0 ] && shf=(--shift-secs "$SHIFT")   # C: сдвиг входа ±3600/±7200 (TK-333)
  mkdir -p "$W/wt/sub/D20/$d"
  for sym in $syms; do
    src=$JRN/$d/wt/sub/D20/$d/approaches-$sym.csv; sp=(); fp=()
    for f in "$SPOT_EPOCH/study/root-$d/$sym-$d.binlog" "$SPOT_EPOCH/study/root-$d/$sym-$d.binlog.zst"; do [ -e "$f" ] && sp+=("$f"); done
    for f in "$W/study/root-$d/$sym-$d.binlog" "$W/study/root-$d/$sym-$d.binlog.zst"; do [ -e "$f" ] && fp+=("$f"); done
    if [ -e "$src" ] && [ ${#sp[@]} -gt 0 ] && [ ${#fp[@]} -gt 0 ]; then
      "$BIN" lob touches-project --in "$src" --out "$W/wt/sub/D20/$d/approaches-$sym.csv" --spot "${sp[@]}" --fut "${fp[@]}" "${shf[@]}" >> "$O/log/$d.project.log" 2>&1 || echo "$d PROJECT_FAIL $sym" >> "$O/fail.txt"
    else echo "$d НЕТ ВХОДА ПРОЕКЦИИ $sym" >> "$O/fail.txt"; fi
  done
fi
[ -e "$RG/$MON/$d.csv" ] || { echo "$d НЕТ РЕЖИМА $RG/$MON" >> "$O/fail.txt"; exit 4; }; ln -s "$RG/$MON" "$W/study/regime"
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
cat "$P/$PLAN.cells" "$P/$PLAN-$ym.cells" > "$O/cells.txt"
grep -E 'tape|cxl' "$O/cells.txt" > "$O/cells-tape.txt"; grep -vE 'tape|cxl' "$O/cells.txt" > "$O/cells-std.txt"
t1=$(date +%s)
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --sigma-from "study/${SIG:-sigma240}")
for pk in std tape; do
  [ -s "$O/cells-$pk.txt" ] || continue
  args=(); while read -r f v; do [ -n "$f" ] || continue; if [ "$f" = --set ]; then n=${v%%:*}; awk -v n="$n" '$2==n{f=1} END{exit !f}' "$O/cells-$pk.txt" || continue; fi; args+=("$f" "$v"); done < <(sort -u "$P/$PLAN.args" "$P/$PLAN-$ym.args")
  tl=(); [ $pk = tape ] && tl=(--tape-log 300)
  cs=(); [ "${SHIFT:-0}" != 0 ] && cs=(--ctx-shift-secs "$SHIFT")
  bs=(); case " ${args[*]} " in *btc_lag_*) bs=(--btc-seconds "${BTCSEC:-/data/tk204/btc-seconds}");; esac   # TK-274 Г-72: наборы btc_lag_<s>s_min требуют секундный ряд BTC; без них аргументов нет (прежние байты)
  (cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols --exit-form none --early-exit-secs off "${args[@]}"     "${tl[@]}" "${bs[@]}" "${cs[@]}" --cells "$O/cells-$pk.txt" --out-dir "b5/out-$d-$pk" > "$O/log/$d.grid-$pk.log" 2>&1) || echo "$d GRID_FAIL $pk" >> "$O/fail.txt"
done
t2=$(date +%s)
mkdir -p "$O/out/$d"; python3 "$(dirname "$0")/merge-packs.py" "$W/b5/out-$d-std" "$W/b5/out-$d-tape" "$O/out/$d" || echo "$d MERGE_FAIL" >> "$O/fail.txt"
echo "$d syms_b1=$(echo "$syms"|grep -c .) grid_s=$((t2-t1))" >> "$OUT/units.txt"
rm -rf "$W"; touch "$O/done"
