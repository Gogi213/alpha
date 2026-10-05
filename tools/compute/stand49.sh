#!/bin/bash
# stand49.sh <БИН> <d15|d01> : пара одновременно на стенде /dev/shm/alpha-stand — A: старый окон-путь, B: ALPHA_SHARED_ENGINE=1
# (режим общего пути: busy-skip on, round-memo off, exit-group off у обоих). Оба прогона — через /data/benchrun.sh stand.
# Гейт: diff -rq каталогов .cellstmp-<сутки> A против B (только сам прогон bounce-grid, без нарезки по наборам).
# Итог: /data/tk049/stand49/<метка>/metrics.txt (wall/user/sys обоих, gate). ALPHA_SHARED_K — порог выбора пути (умолчание 3).
BIN=$1; M=$2; ST=/dev/shm/alpha-stand; H=/data/tk046/jan/home
case $M in d15) D=2026-01-15;; d01) D=2026-01-01;; *) exit 2;; esac
[ -e $ST/.ready ] || { echo нет стенда; exit 2; }
T=$(date +%H%M%S); R=/data/tk049/stand49/$T-$M-$BIN; mkdir -p $R
J=$H/alpha/tmp-p07/cells-by-day/jall-jan-$D.sh
for x in A B; do
  W=/dev/shm/alpha-run/t49-$T-$x; rm -rf $W; mkdir -p $W/b5 $W/bin
  ln -s $ST/study $W/study; ln -s $ST/root $W/root; ln -s /opt/alpha-compute/bin/$BIN $W/bin/alpha-tk044k1-new
  { sed -n 1,3p $J | sed "s#b5/.cellstmp-$D.log#$R/grid-$x.log#"; } | sed "s#--busy-skip off#--busy-skip on --round-memo off#; s#--exit-group on#--exit-group off#" > $W/run.sh
  E=""; [ $x = B ] && E="ALPHA_SHARED_ENGINE=1 ALPHA_SHARED_STATS=1 ALPHA_SHARED_K=${ALPHA_SHARED_K:-3} ${PATHSTATS:+ALPHA_PATH_STATS=1} ${EXTRA}"
  cat > $W/unit.sh <<EOS
cd $W || exit 2
export HOME=$H $E
/usr/bin/time -f "wall_s %e\nuser_s %U\nsys_s %S\nmaxrss_kb %M" -o $R/time-$x.txt bash $W/run.sh > $R/run-$x.out 2> $R/run-$x.err
echo "rc \$?" >> $R/time-$x.txt
EOS
  systemd-run --quiet --wait --collect --unit=t49-$T-$x -p CPUQuota=200% -p Nice=19 /data/benchrun.sh stand bash $W/unit.sh &
done
wait
for x in A B; do echo "== $x"; cat $R/time-$x.txt; done > $R/metrics.txt
diff -rq /dev/shm/alpha-run/t49-$T-A/b5/.cellstmp-$D /dev/shm/alpha-run/t49-$T-B/b5/.cellstmp-$D > $R/diff.txt 2>&1; echo "gate diff_rc $? files $(find /dev/shm/alpha-run/t49-$T-A/b5/.cellstmp-$D -type f | wc -l)" >> $R/metrics.txt
grep -h SHARED_STATS $R/run-B.err | wc -l | sed 's/^/shared_stats_lines /' >> $R/metrics.txt
rm -rf /dev/shm/alpha-run/t49-$T-A /dev/shm/alpha-run/t49-$T-B; touch $R/.done
