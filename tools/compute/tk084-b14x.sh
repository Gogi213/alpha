#!/bin/bash
# tk084-b14x.sh: счёт 242 клеток b14 (TK-040) по XAUUSDT/CLUSDT, мар…окт, из корня /data/tk084/x (после xprep.done). Упрощённый оркестратор tk048-orch-grph.sh:
# та же раскладка месяц→сутки→группы символов (tk048-orch-jobg.sh с корнем x), без замеров; выход /data/tk048/tk040-b14x/<мес>/b5, маркер /data/tk084/x/b14x.done.
# Запуск: bash /data/tk084/submit-b14x.sh (alsched --cls prod). G=2 (2 символа в сутках), P=число ядер заявки.
X=/data/tk084/x; MONTHS=${MONTHS:-mar apr may jun jul aug sep oct}; G=${G:-2}; P=${P:-8}; bin=${BIN:-alpha-b14flag}; lab=${LAB:-tk040-b14x}; DONE=${DONE:-$X/b14x.done}  # LAB/DONE/DAYS_ONLY — для пробы на малом
S=/data/tk048/$lab; R=$S.out; rm -rf $S $R; mkdir -p $S/bin $R
export ALPHA_SKIP_SAME=1 EVENTS_WIDE=1
sed -e 's#/data/tk046#/data/tk084/x#g' /data/tk048/tk048-orch-jobg.sh > $X/bin/orch-jobg.sh
# пустая группа (в сутках символов меньше G: XAU без CL до 24.03) — bounce-grid на пустом списке падает; такая группа считается готовой без счёта
python3 -c "
import sys;p=sys.argv[1];s=open(p).read()
a='s0=\$(date +%s.%N); /usr/bin/time';b='|| { echo \"\$d \$k FAIL\" >> \$R/fail.txt; exit 1; }'
assert s.count(a)==1 and s.count(b)==1
s=s.replace(a,'[ \$(wc -l < \$W/study/root-\$d/instruments.csv) -gt 1 ] && { '+a).replace(b,b+'; }')
open(p,'w').write(s)" $X/bin/orch-jobg.sh || exit 6
ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
[ "$(readlink -f $S/bin/alpha-tk044k1-new)" = /opt/alpha-compute/bin/$bin ] || { echo "BIN_LINK_FAIL $bin" >&2; exit 3; }
for MON in $MONTHS; do
  [ -e $X/$MON/.gen_done ] || SKIP_PREP=1 GEN_ONLY=1 bash $X/bin/tk046-month.sh $MON || { echo "$MON gen FAIL" >> $X/b14x.err; exit 4; }
  H=$X/$MON/home; E=$H/alpha/epochs/e-$MON; M=$S/$MON; mkdir -p $M/b5 $M/g
  for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $M/$b; done
  mkdir -p $M/bin; ln -s /opt/alpha-compute/bin/$bin $M/bin/alpha-tk044k1-new
  # сутки — по данным (root-<сутки>/instruments.csv), не по календарю: units.txt от gen содержит все дни месяца (окт: 31, данные до 02.10)
  for d in $(cat $X/$MON/units.txt); do [ -e $E/study/root-$d/instruments.csv ] && [[ $d =~ ${DAYS_ONLY:-.} ]] && echo "$MON $d"; done
done > $S/days.txt
export HOME=$X/mar/home; cd $S || exit 2
while read -r mon d; do for ((k=0;k<G;k++)); do echo "$mon $d $k"; done; done < $S/days.txt | xargs -P $P -L1 bash -c 'MON=$0 bash '$X'/bin/orch-jobg.sh '$S'/$0 '$R' $1 $2 '$G
n=$(find $S/*/b5 -type f 2>/dev/null | wc -l)
echo "days $(wc -l < $S/days.txt) files $n fail $(cat $R/fail.txt 2>/dev/null | wc -l)" > $DONE.summary
[ -e $R/fail.txt ] && exit 5
touch $DONE
