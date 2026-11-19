#!/usr/bin/env bash
# TK-620: ворота «байт в байт» новых ключей `nx sl cw cl ch cm` (§13.31 п. 3). Запуск на srv под systemd-run (свой юнит; без pkill):
#   systemd-run --unit=tk620-gate --nice=0 bash tk620_gate.sh <NEWBIN> [метка]
# Эталон — alpha-mk.396989e9 (TK-616; на mk=0 побайтно равен a112684a — ворота gb1m). Варианты (новые ключи выкл.): L_base, S_base, ow_e,
# L_mk1, S_mk1, L_mk2, S_mk2; окна wa, wb, wc; P42_DETAIL=1; пустой кэш сигналов у обоих. Сверка: *-trades|daily|coinday|entryrej.csv, *-detail.jsonl,
# *-maker.json (если есть), meta.json (из `params` вырезаются `, tl: 0` и `, mk: 0`). Итог — /data/p42-tk620/gate/<метка>.summary (GATE OK|DIFF|FAIL); rc — в <метка>.rc.
set -uo pipefail
NEW="$1"; TAG="${2:-g1}"
REF=/data/p42-tk616/alpha-mk.396989e9
REFMD5=396989e9
ROOT=/data/p42-tk599; D=/data/p42-tk620; G=$D/gate/$TAG; SUM=$D/gate/$TAG.summary
SYMS=/data/p42-t566/symbols.txt
ST=$ROOT/tick-store
mkdir -p "$G"; : > "$SUM"
[ "$(md5sum < "$REF" | cut -c1-8)" = "$REFMD5" ] || { echo "GATE FAIL эталонный бинарь не $REFMD5" >> "$SUM"; echo 2 > "$D/gate/$TAG.rc"; exit 2; }
LB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20'
SB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05'
OWE=$(grep '^ow_e ' /data/p42-t566/variants-e16.txt | head -1)
V=$G/variants.txt
printf 'L_base %s\nS_base %s\n%s\nL_mk1 %s mk=1\nS_mk1 %s mk=1\nL_mk2 %s mk=2\nS_mk2 %s mk=2\n' "$LB" "$SB" "$OWE" "$LB" "$SB" "$LB" "$SB" > "$V"
FL=(--tick-store "$ST" --tick-store-cap-gb 3 --tick-store-min-free-gb 5)
t0() { case "$1" in wa) echo 2026-06-17;; wb) echo 2026-07-25;; wc) echo 2026-09-01;; esac; }
run() { # бинарь выход окно кэш-сигналов
  local bin="$1" out="$2" w="$3" sc="$4" s=$(date +%s)
  rm -rf "$out"; mkdir -p "$(dirname "$out")"
  env P42_T0="$(t0 "$w")" P42_DETAIL=1 "$bin" breakout tick --cache "$ROOT/$w/cache" --trades "$ROOT/$w/aggtrades" --variants "$V" \
    --symbols "$(cat $SYMS)" --out "$out" --threads 4 "${FL[@]}" --sig-cache "$sc" > "$out.log" 2>&1
  local rc=$?; echo "  rc=$rc за $(( $(date +%s) - s )) с: $(basename "$bin") $w" >> "$SUM"
  return $rc
}
strip() { sed -e 's/, tl: 0//' -e 's/, mk: 0//' "$1"; }
cmpdirs() { # окно A B
  local w="$1" A="$2" B="$3" n=0 d=0 nv=0 vid side f a b
  while read -r vid _; do
    [ -z "$vid" ] && continue; nv=$((nv+1))
    for side in long short; do
      for f in trades.csv daily.csv coinday.csv entryrej.csv detail.jsonl maker.json; do
        a="$A/$vid/$side-$f"; b="$B/$vid/$side-$f"
        [ ! -e "$a" ] && [ ! -e "$b" ] && continue
        n=$((n+1)); if [ ! -e "$a" ] || [ ! -e "$b" ] || ! cmp -s "$a" "$b"; then d=$((d+1)); echo "  DIFF $vid $side-$f" >> "$SUM"; fi
      done
    done
    n=$((n+1)); if [ ! -e "$A/$vid/meta.json" ] || [ ! -e "$B/$vid/meta.json" ] || ! cmp -s <(strip "$A/$vid/meta.json") <(strip "$B/$vid/meta.json"); then d=$((d+1)); echo "  DIFF $vid meta.json" >> "$SUM"; fi
  done < "$V"
  [ "$d" = 0 ] && echo "GATE OK new-vs-alpha-mk.$REFMD5 $w вариантов=$nv файлов=$n различий=0" >> "$SUM" || echo "GATE DIFF new-vs-alpha-mk.$REFMD5 $w вариантов=$nv файлов=$n различий=$d" >> "$SUM"
}
rc=0
for w in wa wb wc; do
  rm -rf "$G/sc-ref" "$G/sc-new"
  echo "== $w $(date +%H:%M:%S)" >> "$SUM"
  run "$REF" "$G/ref/$w" "$w" "$G/sc-ref" || { echo "GATE FAIL ref $w" >> "$SUM"; rc=1; continue; }
  run "$NEW" "$G/new/$w" "$w" "$G/sc-new" || { echo "GATE FAIL new $w" >> "$SUM"; rc=1; continue; }
  cmpdirs "$w" "$G/new/$w" "$G/ref/$w"
  [ "${KEEP_OUT:-0}" = 1 ] || rm -rf "$G/ref/$w" "$G/new/$w" "$G/sc-ref" "$G/sc-new"
done
echo "== done $(date +%H:%M:%S)" >> "$SUM"
grep -q "GATE DIFF\|GATE FAIL" "$SUM" && rc=1
echo "$rc" > "$D/gate/$TAG.rc"
exit "$rc"
