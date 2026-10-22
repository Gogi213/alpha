#!/usr/bin/env bash
# TK-359: гейт `lob seconds` на 10 монето-сутках (сутки D пула, 10 символов по децилям размера бинлога).
# Снимок головы: gate/push-head.sh /data/tk0359/new <дерево>; calc-build.sh и tk359-sums.py лежат в /data/tk0359.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk359-gate --max-runtime 40m --cores 4 --mem 16 -- bash /data/tk0359/tk359-gate.sh
# Гейты: (а) два прогона идентичны (второй — без --check-mids, значит флаг файл не меняет); (б) --check-mids: mid_mismatch=0
# (86 400 × 2 сверки на монето-сутки); (в) tk359-sums.py против `lob trades`. Замер для --est: wall/CPU/RSS каждого прогона.
# Итог: /data/tk0359/summary.txt, метка gate.done.
set -uo pipefail
W=/data/tk0359; D=${D:-2026-02-10}; SRC=${SRC:-/data/tk046/feb/home/alpha/epochs/e-feb/study/root-$D}
S=$W/summary.txt; : > "$S"; rm -f "$W/gate.done"
BIN=$W/new/alpha-new
[ -x "$BIN" ] || { JOBS=4 bash "$W/calc-build.sh" "$W/new" > "$W/build.log" 2>&1 || { echo "build rc≠0" | tee -a "$S"; exit 70; }; }
echo "bin $("$BIN" --version) md5 $(md5sum "$BIN" | cut -c1-32) day $D" >> "$S"
syms=$(ls -S "$SRC"/*-"$D".binlog | awk '{a[NR]=$0}END{for(i=1;i<=10;i++){k=int((i-0.5)*NR/10)+1; print a[k]}}' | sed -E 's|.*/([^/]+)-[0-9-]{10}\.binlog|\1|')
fail=0
for s in $syms; do
  R=$W/roots/$s; rm -rf "$R" "$W/out/$s"; mkdir -p "$R" "$W/out/$s/a" "$W/out/$s/b" "$W/out/$s/t"
  ln -s "$SRC/$s-$D.binlog"* "$R/"; cp "$SRC/verify-$s.status" "$R/" 2>/dev/null
  /usr/bin/time -f "%e %U %S %M" -o "$W/out/$s/time-a" "$BIN" lob seconds --root "$R" --symbol "$s" --day "$D" --out "$W/out/$s/a" --check-mids > "$W/out/$s/a.log" 2>&1; rca=$?
  /usr/bin/time -f "%e %U %S %M" -o "$W/out/$s/time-b" "$BIN" lob seconds --root "$R" --symbol "$s" --day "$D" --out "$W/out/$s/b" > "$W/out/$s/b.log" 2>&1; rcb=$?
  "$BIN" lob trades --root "$R" --symbol "$s" --day "$D" --out "$W/out/$s/t/trades-$s.csv" > "$W/out/$s/t.log" 2>&1; rct=$?
  cmp -s "$W/out/$s/a/$s-$D.csv" "$W/out/$s/b/$s-$D.csv"; ga=$?
  python3 "$W/tk359-sums.py" "$SRC/$s-$D.binlog" "$W/out/$s/b/$s-$D.csv" "$W/out/$s/t/trades-$s.csv" "$D" > "$W/out/$s/sums.txt" 2>&1; gv=$?
  rows=$(($(wc -l < "$W/out/$s/b/$s-$D.csv")-1))
  echo "$s rc=$rca/$rcb/$rct (а)cmp=$ga (в)=$gv rows=$rows | $(grep -h '^seconds:' "$W/out/$s/a.log") | wall,user,sys,rssKB(a)=$(cat "$W/out/$s/time-a") (b)=$(cat "$W/out/$s/time-b") | $(cat "$W/out/$s/sums.txt")" >> "$S"
  [ "$rca$rcb$rct$ga$gv" = 00000 ] && [ "$rows" = 86400 ] || fail=1
done
echo "ИТОГ гейта: $([ $fail = 0 ] && echo ЗЕЛЁНЫЙ || echo КРАСНЫЙ)" >> "$S"
touch "$W/gate.done"; exit $fail
