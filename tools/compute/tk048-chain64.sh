#!/bin/bash
# chain64 (способ 3, В-185): холодная стена суток 2026-01-01 b15col — исходные бинлоги (A) против колоночных контейнеров chain63 (B), порядок A B A2 B2, diff против e-jan/b5.
# Запуск: systemd-run --unit tk048-chain64 --collect bash /data/tk048/chain64.sh ; выход /data/tk048/chain64.txt, маркер /data/tk048/chain64.done.
rm -f /data/tk048/chain64.done /data/tk048/chain64.txt
H=/data/tk046/jan/home; E=$H/alpha/epochs/e-jan; C=/data/tk048/colE; D=2026-01-01
if [ ! -e $C/.ready ]; then
  rm -rf $C; mkdir -p $C/study/root-$D
  for x in $E/* $E/.[!.]*; do b=$(basename $x); [ "$b" = study ] || ln -s $x $C/$b; done
  for x in $E/study/*; do b=$(basename $x); [ "$b" = root-$D ] || ln -s $x $C/study/$b; done
  for f in $E/study/root-$D/*; do b=$(basename $f); case $b in *.binlog|*.binlog.events) ;; *) ln -s $f $C/study/root-$D/$b;; esac; done
  for z in /data/tk048/col/*.binlog.zst; do b=$(basename $z); ln -s $z $C/study/root-$D/$b; set -- $(cat $E/study/root-$D/${b%.zst}.events 2>/dev/null)
    [ -n "$3" ] && echo "$(stat -L -c %s $z) $(stat -L -c %Y $z) $3" > $C/study/root-$D/$b.events; done
  touch $C/.ready
fi
sed 's#E=\$H/alpha/epochs/e-\$MON; S=#E=${EOVR:-$H/alpha/epochs/e-$MON}; S=#' /data/tk048/tk048-orch-grp11.sh > /data/tk048/tk048-orch-grp11c.sh
printf '#!/bin/bash\nexport ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1\n: ${ALPHA_APPROACH_BIN_DIR:=/data/tk052/abin-jf}\nexport ALPHA_APPROACH_BIN_DIR\nexec /opt/alpha-compute/bin/alpha-b15col "$@"\n' > /opt/alpha-compute/bin/alpha-b15colflag; chmod +x /opt/alpha-compute/bin/alpha-b15colflag
cat > /data/tk048/chain64-inner.sh <<'IN'
#!/bin/bash
for r in A B A2 B2; do
  case $r in A*) ov=;; B*) ov=/data/tk048/colE;; esac
  systemd-run --wait --collect --unit tk048-c64$r -p CPUQuota=1500% --setenv=EOVR=$ov --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m bash /data/tk048/tk048-orch-grp11c.sh c64$r alpha-b15colflag 2026-01-01 8 15 > /dev/null 2>&1
  { echo "== $r"; grep -E "wall_s|iowait|core_util|disk_MBps|read_GB|gate|cpu_user|interference|tail_s" /data/tk048/orch-c64$r.out/metrics.txt; } >> /data/tk048/chain64.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain64-inner.sh > /dev/null 2>&1
touch /data/tk048/chain64.done
