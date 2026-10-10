#!/bin/bash
# TK-135/П5: гейт «байт в байт» слоя разбора П-12. Старое дерево (old/) и новое (new/) — одни входы (bundle), md5 всех выходов.
# Запуск на calc (ONLY=<имя шага> — только он): tk135-gate.sh <каталог с old/ и new/>; отчёт <каталог>/report.tsv (потребитель, md5 до, md5 после, равно).
cd "$1" || exit 2
run() {  # run <дерево> <имя> <команда...> -> <дерево>/_out/<имя>.stdout, rc
  t=$1; n=$2; shift 2; mkdir -p $t/_out
  (cd $t && "$@" > _out/$n.stdout 2> _out/$n.stderr; echo $? > _out/$n.rc)
}
both() { n=$1; shift; [ -z "$ONLY" ] || [ "$ONLY" = "$n" ] || return; run old $n "$@" & run new $n "$@" & wait; }
bothx() { n=$1; o=$2; w=$3; [ -z "$ONLY" ] || [ "$ONLY" = "$n" ] || return; run old $n bash -c "$o" & run new $n bash -c "$w" & wait; }   # разные команды (слитые скрипты)
export PYTHONHASHSEED=0
# подготовка деревьев: навык (effective_n) нужен monthly-pnl; в old R2DIR=-a без клеток halflevel — как в 49f55a0d ставим -b (правка входа, одинаково в old/new);
# выходные monthly-pnl в деревьях — симлинки на чужой прогон: убрать, чтобы писать в своё дерево
for t in old new; do
  [ -e $t/.claude ] || { mkdir -p $t/.claude; ln -s ${SKILLS:-/data/tk135gate/new/.claude/skills} $t/.claude/skills; }
  rm -f $t/docs/findings/monthly-pnl-v171c-2026-10-09.csv $t/docs/findings/monthly-pnl-v171c-2026-10-09.md
done
# в old сводка sharpe читается из -A (нет клеток halfstop/halflevel → KeyError); как в a56e1400 — из -D (-A=-D, кроме p_wy 2 клеток e114)
sed -i 's|2026-10-09-v171c-A.csv|2026-10-10-v171c-D.csv|' old/tools/analyze/monthly-pnl.py
sed -i 's|^R2DIR, EXTDIR, R1DIR = "data/p12r2-v171c-a/"|R2DIR, EXTDIR, R1DIR = "data/p12r2-v171c-b/"|' old/tools/analyze/monthly-pnl.py
if [ -n "$ONLY" ]; then rm -f old/_out/$ONLY.* new/_out/$ONLY.* gate-$ONLY.done; else rm -rf old/_out new/_out gate.done; fi; sleep 1; touch old/_start new/_start; sleep 1   # метка: что новее — выход этого прогона
P=${PY:-python3}   # на calc нет numpy: PY=/data/tk135gate/venv/bin/python3
both sharpe-free   $P tools/analyze/p12-sharpe.py free
both sharpe-B2-v171c env P12_POOL_FROM=1 P12_DIR=data/p12r2-v171c-a/ P12_OUT=gate $P tools/analyze/p12-sharpe.py B2
both tiers-free    $P tools/analyze/p12-tiers.py free
both coin-c2       $P tools/analyze/p12-coin-c2.py free
both monthly-pnl   $P tools/analyze/monthly-pnl.py
both tk113-cells   $P tools/analyze/tk113-cells.py
both tk114-testB   $P tools/analyze/tk114-testB.py free
both tk114-tier1   $P tools/analyze/tk114-tier1-select.py free
both tk114-tier2   $P tools/analyze/tk114-tier2-select.py free
both tk114-report2 $P tools/analyze/tk114-report2.py
bothx merge-r2a "$P tools/compute/p12-r2a-merge.py" "$P tools/compute/p12-merge.py r2a"
bothx merge-r3a "$P tools/compute/p12-r3a-merge.py" "$P tools/compute/p12-merge.py r3a"
# отчёт: только выходы — stdout (_out) и файлы, записанные этим прогоном (новее метки _start; входные копии не попадают); сравнение old/new
for t in old new; do (cd $t && find -L _out docs/findings data -type f -newer _start ! -name '*.stderr' ! -name _start | sort | xargs md5sum | sed 's/  /	/') > $t.md5; done
{ echo -e "файл	md5 до	md5 после	равно"
  join -t$'	' -1 2 -2 2 -a1 -a2 -e MISSING -o 0,1.1,2.1 <(sort -t$'	' -k2 old.md5) <(sort -t$'	' -k2 new.md5) | awk -F'	' '{print $0 "	" ($2==$3?"да":"НЕТ")}'
} > report${ONLY:+-$ONLY}.tsv
echo done > gate${ONLY:+-$ONLY}.done
