#!/bin/bash
# TK-135/П5: гейт «байт в байт» слоя разбора П-12. Старое дерево (old/) и новое (new/) — одни входы (bundle), md5 всех выходов.
# Запуск на calc: tk135-gate.sh <каталог с old/ и new/>; отчёт <каталог>/report.tsv (потребитель, md5 до, md5 после, равно).
cd "$1" || exit 2
run() {  # run <дерево> <имя> <команда...> -> <дерево>/_out/<имя>.stdout, rc
  t=$1; n=$2; shift 2; mkdir -p $t/_out
  (cd $t && "$@" > _out/$n.stdout 2> _out/$n.stderr; echo $? > _out/$n.rc)
}
both() { n=$1; shift; run old $n "$@" & run new $n "$@" & wait; }
bothx() { n=$1; o=$2; w=$3; run old $n bash -c "$o" & run new $n bash -c "$w" & wait; }   # разные команды (слитые скрипты)
export PYTHONHASHSEED=0
P=${PY:-python3}   # на calc нет numpy: PY=/data/tk135gate/venv/bin/python3
both sharpe-free   $P tools/compute/p12-sharpe.py free
both sharpe-B2-v171c env P12_POOL_FROM=1 P12_DIR=data/p12r2-v171c-a/ P12_OUT=gate $P tools/compute/p12-sharpe.py B2
both tiers-free    $P tools/compute/p12-tiers.py free
both coin-c2       $P tools/compute/p12-coin-c2.py free
both monthly-pnl   $P tools/compute/monthly-pnl.py
both tk113-cells   $P tools/compute/tk113-cells.py
both tk114-testB   $P tools/compute/tk114-testB.py free
both tk114-tier1   $P tools/compute/tk114-tier1-select.py free
both tk114-tier2   $P tools/compute/tk114-tier2-select.py free
both tk114-report2 $P tools/compute/tk114-report2.py
bothx merge-r2a "$P tools/compute/p12-r2a-merge.py" "$P tools/compute/p12-merge.py r2a"
bothx merge-r3a "$P tools/compute/p12-r3a-merge.py" "$P tools/compute/p12-merge.py r3a"
# отчёт: md5 каждого файла выхода (stdout + docs/findings/_new + data/*), сравнение old/new
for t in old new; do (cd $t && find _out docs/findings data/p12r2-a data/p12r2-v171c-a data/p12r2-v171c-b -type f ! -name '*.stderr' -printf '%p\n' | sort | xargs md5sum | sed 's/  /\t/') > $t.md5; done
{ echo -e "файл\tmd5 до\tmd5 после\tравно"
  join -t$'\t' -1 2 -2 2 -a1 -a2 -e MISSING -o 0,1.1,2.1 <(sort -t$'\t' -k2 old.md5) <(sort -t$'\t' -k2 new.md5) | awk -F'\t' '{print $0 "\t" ($2==$3?"да":"НЕТ")}'
} > report.tsv
echo done > gate.done
