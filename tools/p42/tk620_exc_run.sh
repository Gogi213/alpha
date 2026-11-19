#!/usr/bin/env bash
# TK-620 шаг 0 (§13.31 п. 3): дамп P42_EXC=<горизонт>:<W> на L_base и S_base, окна wa/wb/wc, P&L не считается (P42_SIGSX_ONLY=1).
#   systemd-run --unit=tk620-exc --nice=0 bash tk620_exc_run.sh <NEWBIN> [горизонт_мин=240] [W=60] [метка=exc] [кэш-сигналов-для-тёплого-прогона] [метка-холодного-для-сверки]
# Холодный прогон (по умолчанию) — свой пустой кэш сигналов $D/<метка>.sc (кэш TK-599 не трогается). Тёплый — четвёртый/пятый аргументы: тот же кэш + метка холодного;
# после прогона все *-exc.csv тёплого сверяются побайтно с холодным (ворота холодный = тёплый, §13.31): результат — /data/p42-tk620/<метка>.cmp, rc ≠ 0 при расхождении.
# Выход: /data/p42-tk620/<метка>/<окно>/<вариант>/<side>-exc.csv; код возврата — /data/p42-tk620/<метка>.rc (0 — всё); лог — <метка>.log.
set -uo pipefail
NEW="$1"; HZ="${2:-240}"; W="${3:-60}"; TAG="${4:-exc}"; WARMSC="${5:-}"; COLD="${6:-}"
ROOT=/data/p42-tk599; D=/data/p42-tk620; O=$D/$TAG
SYMS=/data/p42-t566/symbols.txt
mkdir -p "$D"; rm -rf "$O"; mkdir -p "$O"; : > "$D/$TAG.log"
if [ -n "$WARMSC" ]; then SC="$WARMSC"; [ -d "$SC" ] || { echo "нет кэша $SC" >> "$D/$TAG.log"; echo 3 > "$D/$TAG.rc"; exit 3; }; else SC="$D/$TAG.sc"; rm -rf "$SC"; fi
LB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20'
SB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05'
printf 'L_base %s\nS_base %s\n' "$LB" "$SB" > "$O/variants.txt"
t0() { case "$1" in wa) echo 2026-06-17;; wb) echo 2026-07-25;; wc) echo 2026-09-01;; esac; }
rc=0
for w in wa wb wc; do
  env P42_T0="$(t0 $w)" P42_SIGSX_ONLY=1 P42_EXC="$HZ:$W" "$NEW" breakout tick --cache "$ROOT/$w/cache" --trades "$ROOT/$w/aggtrades" \
    --variants "$O/variants.txt" --symbols "$(cat $SYMS)" --out "$O/$w" --threads 4 \
    --tick-store "$ROOT/tick-store" --tick-store-cap-gb 3 --tick-store-min-free-gb 5 --sig-cache "$SC" >> "$D/$TAG.log" 2>&1
  r=$?; echo "$w rc=$r $(date +%H:%M:%S)" >> "$D/$TAG.log"; [ "$r" = 0 ] || rc=$r
done
if [ -n "$COLD" ]; then
  n=0; d=0
  for f in "$D/$COLD"/*/*/*-exc.csv; do
    r="${f#$D/$COLD/}"; n=$((n+1)); cmp -s "$f" "$O/$r" || { d=$((d+1)); echo "DIFF $r" >> "$D/$TAG.cmp"; }
  done
  echo "холодный($COLD) против тёплого($TAG): файлов=$n различий=$d" >> "$D/$TAG.cmp"; [ "$d" = 0 ] && [ "$n" -gt 0 ] || rc=1
fi
echo "$rc" > "$D/$TAG.rc"
exit "$rc"
