#!/usr/bin/env bash
# TK-607: ворота «дамп сигналов» (`P42_SIGSX_ONLY=1 P42_SIGSX=14:73`, как у потока признаков TK-594) для L1+L2. Запуск на srv:
#   systemd-run --unit=tk607-gate-sx --nice=0 -p CPUWeight=1000 bash /data/p42-tk607/tk607_gate_sigsx.sh <НОВЫЙ БИНАРЬ> <ПРЕЖНИЙ БИНАРЬ alpha-master> <метка> <окно wa|wb|wc> [склад сделок]
# Варианты — первая строка каждого пакета TK-599 `var/b00NN.txt` (по одному сигнальному набору с пакета). Три прогона одного окна:
#   R0 прежний бинарь (a03c91df, без склада и кэша — независимый эталон), R1 новый + склад + пустой кэш сигналов (холодный), R2 новый + тот же кэш (тёплый).
# Сверка `diff -rq` всех выходов R1 и R2 с R0 (`<сторона>-sigsx.csv`, `meta.json`, пр.). Выходы после сверки удаляются, остаются md5 и журналы.
# Итог — строки GATE OK|DIFF в /data/p42-tk607/gate/<метка>.summary.
set -uo pipefail
NEW="$1"; OLD="$2"; TAG="$3"; W="$4"; ST="${5:-/data/p42-tk607/store-l1}"
D=/data/p42-tk607; ROOT=/data/p42-tk599; G=$D/gate/$TAG; SUM=$D/gate/$TAG.summary
case "$W" in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; *) echo "окно?"; exit 2;; esac
mkdir -p "$G"; : > "$SUM"
VARS=$G/variants.txt; : > "$VARS"
for f in $ROOT/var/b00*.txt; do head -1 "$f" >> "$VARS"; done
SC=$D/sigc-$TAG; rm -rf "$SC"
echo "== $TAG $W вариантов $(wc -l < "$VARS") $(date +%H:%M:%S)" >> "$SUM"
run() { # метка бинарь [ключи движка...]
  local label="$1" bin="$2"; shift 2
  local out="$G/$label"; rm -rf "$out"; local s; s=$(date +%s)
  env P42_T0=$T0 P42_SIGSX_ONLY=1 P42_SIGSX=14:73 "$bin" breakout tick --cache "$ROOT/$W/cache" --trades "$ROOT/$W/aggtrades" --variants "$VARS" \
    --symbols "$(cat /data/p42-t566/symbols.txt)" --out "$out" --threads 4 "$@" > "$G/$label.log" 2>&1
  echo "$label rc=$? за $(( $(date +%s) - s )) с" >> "$SUM"
  grep -E "^фаза 1 готова|^готово:|^tick-store|^sig-cache" "$G/$label.log" | sed 's/^/  /' >> "$SUM"
}
run r0-old "$OLD"
FL=(--tick-store "$ST" --tick-store-cap-gb "${CAP_GB:-8}" --tick-store-min-free-gb 5 --sig-cache "$SC")
run r1-cold "$NEW" "${FL[@]}"
run r2-warm "$NEW" "${FL[@]}"
n=$(find "$G/r0-old" -type f | wc -l)
for r in r1-cold r2-warm; do
  if diff -rq "$G/r0-old" "$G/$r" > "$G/$r.diff" 2>&1; then
    echo "GATE OK $TAG $W $r файлов=$n различий=0" >> "$SUM"
  else
    echo "GATE DIFF $TAG $W $r различий=$(wc -l < "$G/$r.diff") (см. $G/$r.diff)" >> "$SUM"
  fi
done
( cd "$G/r0-old" && find . -type f -print0 | sort -z | xargs -0 md5sum ) > "$G/r0-old.md5"
if ! grep -q "GATE DIFF" "$SUM"; then rm -rf "$G/r0-old" "$G/r1-cold" "$G/r2-warm"; fi
echo "== done $(date +%H:%M:%S)" >> "$SUM"
