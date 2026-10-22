#!/usr/bin/env bash
# TK-279 п.5 (Судья smoke-fork): смоук Г-135/136/137 на 1 сутках (2026-03-16, 103 клетки), PGO r2: 0 клеток без сигналов либо объяснение.
#   план /data/tk0279/p17plan-s5 (L.cells = g136+g137 58 строк, L-2026-03.cells = g135 postsweep 45 строк), выход /data/tk0279/p17s5b/out
#   Журнал подходов — НОВЫЙ (ABN3: repeat_count_at_arm, swept_lots): approaches-* из п.1а (/data/tk0279/appr1/new), остальное — ссылки на
#   старый журнал; со старым журналом repeat_min/postsweep дают 0 сигналов молча (смоук 1017004154793, 96 из 103 клеток пусты).
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов (нет /data/tk0279/pgo.ok)"; exit 75; }
d=2026-03-16; OLD=/data/p12r3a/jrn/$d/wt/sub/D20/$d; NEW=/data/tk0279/appr1/new/$d; JR=/data/tk0279/jrn-s5; R=/data/tk0279/p17s5b
ls "$NEW"/approaches-*.csv > /dev/null || { echo "нет новых подходов $NEW"; exit 4; }
rm -rf "$JR"; mkdir -p "$JR/$d/wt/sub/D20/$d"
for f in "$OLD"/*; do ln -s "$f" "$JR/$d/wt/sub/D20/$d/"; done
for f in "$NEW"/approaches-*.csv; do ln -sf "$f" "$JR/$d/wt/sub/D20/$d/"; done
: > "$JR/$d/done"
export REASON="TK-279 смоук Г-135/136/137, r2 pgo, новые подходы" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=1 GT=${GT:-3} PLANSRC=/data/tk0279/p17plan-s5
export JRN=$JR ALT_JRN=
echo "mar $d" > /data/tk0279/p17s5b-days.txt
bash /data/tk0279/p17-count.sh "$R" /data/tk0279/p17s5b-days.txt
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
