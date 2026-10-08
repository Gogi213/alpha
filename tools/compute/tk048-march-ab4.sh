#!/bin/bash
# TK-048 A/B одним бинарником alpha-b22 (флаг ALPHA_DIRECT_FEED 0=A, 1=B), гейт — diff -rq против выхода тёплого прогона W0 (флаг 0), не против TK-040 (список монет другой). Копия шаблона tk048-march-ab.sh. Исходно: A/B вне янв+фев (В-197, правило Судьи 07.10): сутки марта, ВСЕ монеты, кандидат против лучшего, ABBA после тёплого прогона. Гейт — diff -rq против готового счёта TK-040 (/data/tk048/tk040-b14/mar/b5).
# Запуск: systemd-run --unit tk048-marab --collect /data/tk052/benchrun2.sh stand bash /data/tk048/march-ab4.sh [A_bin B_bin day]; выход /data/tk048/march-ab4.txt, маркер march-ab4.done.
A=${1:-alpha-b22-f0}; B=${2:-alpha-b22-f1}; d=${3:-2026-03-02}
G=/data/tk048/mab4; OUT=/data/tk048/march-ab4.txt; MON=mar; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; REF=/data/tk048/tk040-b14/mar/b5
rm -f /data/tk048/march-ab4.done $OUT; rm -rf $G; mkdir -p $G
run() { v=$1; bin=$2; S=$G/$v
  mkdir -p $S/b5 $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
  for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
  grep -v '^cp b5/.cellstmp-.*[.]log /data' $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh > $S/day.sh
  ( export HOME=$H ALPHA_SKIP_SAME=1; cd $S; t0=$(date +%s.%N); bash $S/day.sh > run.out 2> run.err; rc=$?; t1=$(date +%s.%N)
    echo "$v $bin $d rc $rc wall_s $(echo "$t1 - $t0" | bc)" >> $OUT ) ; }
gate() { v=$1; if [ $v = W0 ]; then rm -rf $G/ref; mv $G/W0/b5 $G/ref; echo "gate W0 ref_files $(find $G/ref -type f | wc -l)" >> $OUT; return; fi
  r=$(diff -rq $G/$v/b5 $G/ref 2>&1 | wc -l); n=$(find $G/$v/b5 -type f | wc -l); echo "gate $v files $n diff_lines_vs_W0 $r" >> $OUT; rm -rf $G/$v/b5; }
run W0 $A; gate W0
run A1 $A; gate A1
run B1 $B; gate B1
run B2 $B; gate B2
run A2 $A; gate A2
touch /data/tk048/march-ab4.done
