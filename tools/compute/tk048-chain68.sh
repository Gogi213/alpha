#!/bin/bash
# chain68 (способ 3, В-185): холодная волна 01–15.01 (конфиг chain48 ac: P=20, grp16, HOIST+WARM_BG, abin-t46) — исходные бинлоги (A) против колоночных контейнеров (B), один бинарник alpha-b15colflag, порядок A B A2 B2, gate cmp против e-jan/b5.
# Нужны контейнеры chain63 (/data/tk048/col, сутки 01) и chain67 (/data/tk048/col15/<сутки>/, 02–15). Запуск: systemd-run --unit tk048-chain68 --collect bash /data/tk048/chain68.sh ; выход /data/tk048/chain68.txt, маркер chain68.done.
rm -f /data/tk048/chain68.done /data/tk048/chain68.txt
H=/data/tk046/jan/home; E=$H/alpha/epochs/e-jan; C=/data/tk048/colE15
for n in 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do [ -e /data/tk048/col15/2026-01-$n/.ok ] || { echo "NO_CONTAINERS 2026-01-$n" >> /data/tk048/chain68.txt; exit 3; }; done
if [ ! -e $C/.ready ]; then
  rm -rf $C; mkdir -p $C/study
  for x in $E/* $E/.[!.]*; do b=$(basename $x); [ "$b" = study ] || ln -s $x $C/$b; done
  for x in $E/study/*; do b=$(basename $x); case $b in root-2026-01-0[1-9]|root-2026-01-1[0-5]) ;; *) ln -s $x $C/study/$b;; esac; done
  for n in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do
    D=2026-01-$n; mkdir -p $C/study/root-$D; Z=/data/tk048/col15/$D; [ $n = 01 ] && Z=/data/tk048/col
    for f in $E/study/root-$D/*; do b=$(basename $f); case $b in *.binlog|*.binlog.events) ;; *) ln -s $f $C/study/root-$D/$b;; esac; done
    for z in $Z/*.binlog.zst; do b=$(basename $z); ln -s $z $C/study/root-$D/$b; set -- $(cat $E/study/root-$D/${b%.zst}.events 2>/dev/null)
      [ -n "$3" ] && echo "$(stat -L -c %s $z) $(stat -L -c %Y $z) $3" > $C/study/root-$D/$b.events; done
  done
  touch $C/.ready
fi
sed 's#E=\$H/alpha/epochs/e-\$MON; S=#E=${EOVR:-$H/alpha/epochs/e-$MON}; S=#' /data/tk048/tk048-orch-grp16.sh > /data/tk048/tk048-orch-grp16c.sh
cat > /data/tk048/chain68-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in A B A2 B2; do
  case $r in A*) ov=;; B*) ov=/data/tk048/colE15;; esac
  systemd-run --wait --collect --unit tk048-c68$r -p CPUQuota=1500% --setenv=EOVR=$ov --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16c.sh c68$r alpha-b15colflag $D15 8 20 > /dev/null 2>&1
  { echo "== $r"; grep -E "wall_s|iowait|core_util|disk_MBps|read_GB|gate|interference|tail_s|validity" /data/tk048/orch-c68$r.out/metrics.txt; } >> /data/tk048/chain68.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain68-inner.sh > /dev/null 2>&1
touch /data/tk048/chain68.done
