#!/bin/bash
# stand49ad.sh <БИН> <d15|d01> : пара — A: ALPHA_FAST_HOLD=1 SKIP_NOSIGNAL=1 (b11), B: + ALPHA_ADMIT_CACHE=1 ${EXTRA}, A: + ${AEXTRA}; WIDE=1 для --events wide; каталог /data/tk049/stand49fb
# (режим общего пути: busy-skip on, round-memo off, exit-group off у обоих). Оба прогона — через /data/benchrun.sh stand.
# Гейт: diff -rq каталогов .cellstmp-<сутки> A против B (только сам прогон bounce-grid, без нарезки по наборам).
# ONLY=B — только B; PERF=1 — perf record в $R/perf-<x>.data. CMPWIDE=1: A compact, B wide, оба без ALPHA_FAST_BOOK (diff режимов). Итог: /data/tk049/stand49fb/<метка>/metrics.txt (wall/user/sys обоих, gate). ALPHA_SHARED_K — порог выбора пути (умолчание 3).
BIN=$1; M=$2; ST=/dev/shm/alpha-stand; H=/data/tk046/jan/home
case $M in d15) D=2026-01-15;; d01) D=2026-01-01;; *) exit 2;; esac
[ -e $ST/.ready ] || { echo нет стенда; exit 2; }
T=$(date +%H%M%S); R=/data/tk049/stand49fb/$T-$M-$BIN; mkdir -p $R
J=$H/alpha/tmp-p07/cells-by-day/jall-jan-$D.sh
for x in ${ONLY:-A B}; do
  WD=$WIDE; [ -n "$CMPWIDE" ] && { [ $x = B ] && WD=1 || WD=; }
  W=/dev/shm/alpha-run/t49fb-$T-$x; rm -rf $W; mkdir -p $W/b5 $W/bin
  ln -s $ST/study $W/study; ln -s $ST/root $W/root; ln -s /opt/alpha-compute/bin/$BIN $W/bin/alpha-tk044k1-new
  { sed -n 1,3p $J | sed "s#b5/.cellstmp-$D.log#$R/grid-$x.log#"; } | sed "${WD:+s# bounce-grid # bounce-grid --events wide #;}s#--busy-skip off#--busy-skip on --round-memo off#; s#--exit-group on#--exit-group off#" > $W/run.sh
  E="ALPHA_FAST_HOLD=1 ALPHA_SKIP_NOSIGNAL=1"; [ $x = A ] && E="$E ${AEXTRA}"; [ $x = B ] && E="$E ALPHA_ADMIT_CACHE=1 ${EXTRA}"
  cat > $W/unit.sh <<EOS
cd $W || exit 2
export HOME=$H $E
/usr/bin/time -f "wall_s %e\nuser_s %U\nsys_s %S\nmaxrss_kb %M" -o $R/time-$x.txt ${PERF:+perf record -F 499 -g -o $R/perf-$x.data --} bash $W/run.sh > $R/run-$x.out 2> $R/run-$x.err
echo "rc \$?" >> $R/time-$x.txt
EOS
  systemd-run --quiet --wait --collect --unit=t49fb-$T-$x -p CPUQuota=200% -p Nice=19 /data/benchrun.sh stand bash $W/unit.sh &
done
wait
for x in ${ONLY:-A B}; do echo "== $x"; cat $R/time-$x.txt; done > $R/metrics.txt
[ -z "$ONLY" ] && diff -rq /dev/shm/alpha-run/t49fb-$T-A/b5/.cellstmp-$D /dev/shm/alpha-run/t49fb-$T-B/b5/.cellstmp-$D > $R/diff.txt 2>&1; echo "gate diff_rc $? files $(find /dev/shm/alpha-run/t49fb-$T-A/b5/.cellstmp-$D -type f | wc -l)" >> $R/metrics.txt
rm -rf /dev/shm/alpha-run/t49fb-$T-A /dev/shm/alpha-run/t49fb-$T-B; touch $R/.done
