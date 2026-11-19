#!/usr/bin/env bash
# TK-616: ворота G-B1 (§13.28 п. 4). Запускать на srv под systemd-run (свой юнит; без pkill):
#   systemd-run --unit=tk616-gb1 --nice=0 bash tk616_gate.sh <NEWBIN> [метка]
# Что гоняет (окна wa, wb, wc; сделки — склад TK-599, свой кэш сигналов на каждый ряд):
#   ref   — a112684a (md5 проверяется) на L_base, S_base, ow_e, P42_DETAIL=1 (эталон — выходы этого же бинаря на тех же входах);
#   new0c — новый бинарь, mk=0, пустой кэш сигналов; побайтно против ref: *-trades|daily|coinday|entryrej.csv, *-detail.jsonl, meta.json;
#   new0w — то же на тёплом кэше (после new0c);
#   mkc   — L_mk1, S_mk1, L_mk2, S_mk2 (= база стороны + mk=1|2), пустой кэш; mkw — тёплый кэш (сигналы баз уже в кэше);
#           сверка mkc против mkw: *-trades|daily|coinday|entryrej.csv и *-maker.json (detail — тоже, если есть).
# Итог — /data/p42-tk616/gate/<метка>.summary (строки GATE OK|DIFF|FAIL). Выходы после сверки удаляются (KEEP_OUT=1 — оставить).
set -uo pipefail
NEW="$1"; TAG="${2:-gb1}"
OLD=/data/p42-tk607/bin/alpha-l1
OLDMD5=a112684a495e61cf5219610e4eecff22
ROOT=/data/p42-tk599; D=/data/p42-tk616; G=$D/gate/$TAG; SUM=$D/gate/$TAG.summary
SYMS=/data/p42-t566/symbols.txt
ST=$ROOT/tick-store
mkdir -p "$G"; : > "$SUM"
[ "$(md5sum < "$OLD" | cut -c1-32)" = "$OLDMD5" ] || { echo "GATE FAIL эталонный бинарь не $OLDMD5" >> "$SUM"; exit 2; }
LB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20'
SB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05'
OWE=$(grep '^ow_e ' /data/p42-t566/variants-e16.txt | head -1)
V0=$G/variants-mk0.txt; VM=$G/variants-mk.txt
printf 'L_base %s\nS_base %s\n%s\n' "$LB" "$SB" "$OWE" > "$V0"
printf 'L_mk1 %s mk=1\nS_mk1 %s mk=1\nL_mk2 %s mk=2\nS_mk2 %s mk=2\n' "$LB" "$SB" "$LB" "$SB" > "$VM"
FL=(--tick-store "$ST" --tick-store-cap-gb 3 --tick-store-min-free-gb 5)
t0() { case "$1" in wa) echo 2026-06-17;; wb) echo 2026-07-25;; wc) echo 2026-09-01;; esac; }
run() { # бинарь выход окно варианты кэш-сигналов
  local bin="$1" out="$2" w="$3" vars="$4" sc="$5" s=$(date +%s)
  rm -rf "$out"; mkdir -p "$(dirname "$out")"
  env P42_T0="$(t0 "$w")" P42_DETAIL=1 "$bin" breakout tick --cache "$ROOT/$w/cache" --trades "$ROOT/$w/aggtrades" --variants "$vars" \
    --symbols "$(cat $SYMS)" --out "$out" --threads 4 "${FL[@]}" --sig-cache "$sc" > "$out.log" 2>&1
  local rc=$?; echo "  rc=$rc за $(( $(date +%s) - s )) с: $(basename "$bin") $w $(basename "$out")" >> "$SUM"
  return $rc
}
cmpdirs() { # метка окно варианты A B список-файлов...
  local label="$1" w="$2" vars="$3" A="$4" B="$5"; shift 5
  local n=0 d=0 nv=0 vid side f a b
  while read -r vid _; do
    [ -z "$vid" ] && continue; nv=$((nv+1))
    for side in long short; do
      for f in "$@"; do
        a="$A/$vid/$side-$f"; b="$B/$vid/$side-$f"
        [ ! -e "$a" ] && [ ! -e "$b" ] && continue
        n=$((n+1)); if [ ! -e "$a" ] || [ ! -e "$b" ] || ! cmp -s "$a" "$b"; then d=$((d+1)); echo "  DIFF $vid $side-$f" >> "$SUM"; fi
      done
    done
    if [[ " $* " == *" meta.json "* ]]; then
      n=$((n+1)); if [ ! -e "$A/$vid/meta.json" ] || [ ! -e "$B/$vid/meta.json" ] || ! cmp -s "$A/$vid/meta.json" "$B/$vid/meta.json"; then d=$((d+1)); echo "  DIFF $vid meta.json" >> "$SUM"; fi
    fi
  done < "$vars"
  [ "$d" = 0 ] && echo "GATE OK $label $w вариантов=$nv файлов=$n различий=0" >> "$SUM" || echo "GATE DIFF $label $w вариантов=$nv файлов=$n различий=$d" >> "$SUM"
}
tk599cmp() { # окно: выходы a112684a (ref) против готовых выходов TK-599 (a03c91df) на L_base, S_base — основание для guard.py equiv
  local w="$1" n=0 d=0 pair nm vid side f a b
  for pair in "L_base c53a116125b39" "S_base c10fbba7be522"; do
    read -r nm vid <<< "$pair"
    for side in long short; do
      for f in trades.csv daily.csv coinday.csv entryrej.csv; do
        a="$G/ref/$w/$nm/$side-$f"; b="$ROOT/out/$w/$vid/$side-$f"; n=$((n+1))
        cmp -s "$a" "$b" || { d=$((d+1)); echo "  DIFF ref(a112684a) $nm $side-$f vs TK-599(a03c91df)" >> "$SUM"; }
      done
    done
    n=$((n+1)); cmp -s <(grep -v '^  "id":' "$G/ref/$w/$nm/meta.json") <(grep -v '^  "id":' "$ROOT/out/$w/$vid/meta.json") || { d=$((d+1)); echo "  DIFF ref(a112684a) $nm meta.json vs TK-599(a03c91df)" >> "$SUM"; }   # строка id — имя варианта (L_base против хэша), остальное побайтно
  done
  [ "$d" = 0 ] && echo "GATE OK a112684a-vs-a03c91df $w вариантов=2 файлов=$n различий=0" >> "$SUM" || echo "GATE DIFF a112684a-vs-a03c91df $w вариантов=2 файлов=$n различий=$d" >> "$SUM"
}
F0=(trades.csv daily.csv coinday.csv entryrej.csv detail.jsonl meta.json)
FM=(trades.csv daily.csv coinday.csv entryrej.csv maker.json detail.jsonl)
SCN=$G/sc-new0; SCM=$G/sc-mkc
for w in wa wb wc; do
  rm -rf "$SCN" "$SCM" "$G/sc-ref"
  echo "== $w $(date +%H:%M:%S)" >> "$SUM"
  run "$OLD" "$G/ref/$w" "$w" "$V0" "$G/sc-ref" || { echo "GATE FAIL ref $w" >> "$SUM"; continue; }
  tk599cmp "$w"
  [ "${ONLY_EQUIV:-0}" = 1 ] && { rm -rf "$G/ref/$w" "$G/sc-ref"; continue; }   # ONLY_EQUIV=1 — только сверка a112684a с готовыми выходами TK-599
  run "$NEW" "$G/new0c/$w" "$w" "$V0" "$SCN" && cmpdirs "mk0-cold" "$w" "$V0" "$G/new0c/$w" "$G/ref/$w" "${F0[@]}" || echo "GATE FAIL mk0-cold $w" >> "$SUM"
  run "$NEW" "$G/new0w/$w" "$w" "$V0" "$SCN" && cmpdirs "mk0-warm" "$w" "$V0" "$G/new0w/$w" "$G/ref/$w" "${F0[@]}" || echo "GATE FAIL mk0-warm $w" >> "$SUM"
  run "$NEW" "$G/mkc/$w" "$w" "$VM" "$SCM" || echo "GATE FAIL mk-cold $w" >> "$SUM"
  run "$NEW" "$G/mkw/$w" "$w" "$VM" "$SCN" && cmpdirs "mk-cold-vs-warm" "$w" "$VM" "$G/mkc/$w" "$G/mkw/$w" "${FM[@]}" || echo "GATE FAIL mk-warm $w" >> "$SUM"
  [ "${KEEP_OUT:-0}" = 1 ] || rm -rf "$G/new0c/$w" "$G/new0w/$w" "$G/mkc/$w" "$G/mkw/$w" "$G/ref/$w" "$SCN" "$SCM" "$G/sc-ref"
done
echo "== done $(date +%H:%M:%S)" >> "$SUM"
