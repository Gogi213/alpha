#!/bin/bash
# stand.sh <бин> <atom|d15|d01> [VAR=val ...]       — один прогон на стенде в памяти (/dev/shm/alpha-stand), БЕЗ замка, nice 19, CPUQuota 200 %
# stand.sh pair <бинА> <бинБ> <atom|d15|d01> [VAR=val ...] — два прогона одновременно в одинаковых условиях (времена — только парой)
#   atom: ATOMUSDT суток 01-15, diff -r против /data/tk049/ATOMUSDT-base/cells; d15/d01: все символы суток, cmp файлов b5 против эталона e-jan/b5.
#   Вывод: /data/tk051/stand-out/<метка>/metrics.txt (wall/user/sys + 'gate files N diff M'); KEEP=1 — оставить рабочий каталог.
#   Стенд создаёт stand-setup.sh (маркер /dev/shm/alpha-stand/.ready). Переменные (ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 — связка b1) — аргументами VAR=val.
ST=/dev/shm/alpha-stand; H=/data/tk046/jan/home; E=$H/alpha/epochs/e-jan; O=/data/tk051/stand-out
if [ "$1" = pair ]; then
  A=$2; B=$3; M=$4; shift 4; T=$(date +%H%M%S)
  bash "$0" "$A" "$M" "TAG=$T-A" "$@" & bash "$0" "$B" "$M" "TAG=$T-B" "$@" & wait
  for t in A B; do echo "== $T-$t"; cat $O/$T-$t-*/metrics.txt; done; exit 0
fi
BIN=$1; M=$2; shift 2; TAG=$(date +%H%M%S)
ENVV=(); for v in "$@"; do case $v in TAG=*) TAG=${v#TAG=};; *) ENVV+=("$v");; esac; done
[ -e $ST/.ready ] || { echo "стенд не готов: $ST/.ready"; exit 2; }
case $M in atom|d15) D=2026-01-15;; d01) D=2026-01-01;; *) echo "режим atom|d15|d01"; exit 2;; esac
L=$TAG-$M-$BIN; R=$O/$L; W=/dev/shm/alpha-run/$L; rm -rf $R $W; mkdir -p $R $W/b5 $W/bin
ln -s $ST/study $W/study; ln -s $ST/root $W/root; ln -s /opt/alpha-compute/bin/$BIN $W/bin/alpha-tk044k1-new
J=$H/alpha/tmp-p07/cells-by-day/jall-jan-$D.sh
if [ $M = atom ]; then
  sed -n 3p $J | sed "s# lob bounce-grid # lob bounce-grid --symbol ATOMUSDT #; s#b5/.cellstmp-$D.log#$R/grid.log#; s#b5/.cellstmp-$D#b5/.cellstmp-ATOMUSDT#; s#--extra-runs [^ ]*##" > $W/run.sh
  ENVV+=(ALPHA_ATTEMPT_STATS=1)
else
  { echo "set -e"; sed -n 3p $J | sed "s#b5/.cellstmp-$D.log#$R/grid.log#"; sed -n '5,$p' $J; } > $W/run.sh
fi
cat > $W/unit.sh <<EOS
cd $W || exit 2
export HOME=$H ${ENVV[*]}
/usr/bin/time -f "wall_s %e\nuser_s %U\nsys_s %S\nmaxrss_kb %M" -o $R/time.txt bash $W/run.sh > $R/run.out 2> $R/run.err
echo "rc \$?" >> $R/time.txt
EOS
systemd-run --quiet --wait --collect --unit=stand-$L -p CPUQuota=200% -p Nice=19 bash $W/unit.sh
cp $R/time.txt $R/metrics.txt
cd $W
if [ $M = atom ]; then diff -r /data/tk049/ATOMUSDT-base/cells b5/.cellstmp-ATOMUSDT > $R/diff.txt 2>&1; echo "gate diff_rc $?" >> $R/metrics.txt
else bad=0; n=0; cd b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || bad=$((bad+1)); done < <(find . -type f -path "*$D*" | grep -v "/\.[^/]")
  echo "gate files $n diff $bad" >> $R/metrics.txt; fi
cd /; [ -n "$KEEP" ] || rm -rf $W
