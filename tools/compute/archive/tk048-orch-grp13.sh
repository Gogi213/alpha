#!/bin/bash
# tk048-orch-grp.sh <метка> <бинарник> <сутки,...> <G> [P]: малые единицы (сутки × группа из G подряд идущих символов пула), общая очередь xargs -P, холодный кэш.
# grp9: выход (b5/, g/) пишется в /dev/shm, после метрик переносится на sdb, гейт сравнивает перенесённое. Метрики и гейт — как tk048-orch-base.sh. Вывод /data/tk048/orch-<метка>.out
lab=$1; bin=$2; days=${3//,/ }; G=$4; P=${5:-15}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk048/orch-$lab; R=$S.out
SH=/dev/shm/orch-$lab; rm -rf $S $R $SH; mkdir -p $SH/b5 $SH/g $S $R; ln -s $SH/b5 $S/b5; ln -s $SH/g $S/g
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
mkdir $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
[ "$(readlink -f $S/bin/alpha-tk044k1-new)" = /opt/alpha-compute/bin/$bin ] || { echo "BIN_LINK_FAIL $bin" >&2; exit 3; }
export HOME=$H; cd $S || exit 2
bash /data/tk048/tk048-tail.sh snap > $R/cg0.txt
[ -n "$NODROP" ] || { sync; echo 3 > /proc/sys/vm/drop_caches; }
cached0=$(awk '/^Cached/{print $2}' /proc/meminfo); it0=$(awk '$3=="sdb"{print $13}' /proc/diskstats); ia0=$(awk '$3=="sda"{print $13}' /proc/diskstats)
cpu0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd0=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra0=$(awk '$3=="sda"{print $6}' /proc/diskstats)
bash /data/tk048/tk048-sample.sh $R/samples.tsv & SMP=$!; U=tk048-$lab.service; c0=$(systemctl show -p CPUUsageNSec --value $U); t0=$(date +%s.%N)
CG=/sys/fs/cgroup/system.slice/$U; us0=$(awk '/^user_usec/{print $2}' $CG/cpu.stat); sy0=$(awk '/^system_usec/{print $2}' $CG/cpu.stat)
perf stat -a -I 5000 -x, -e instructions,cycles,cache-misses,cache-references -o $R/perf.txt sleep 100000 & PERFPID=$!
( sleep ${PROOF_AFTER:-60}; for pid in $(pgrep -f alpha-tk044k1-new); do case "$(readlink /proc/$pid/cwd)" in $S/*) { echo "proof_pid $pid"; echo "proof_exe $(readlink /proc/$pid/exe)"; echo "proof_md5 $(md5sum < /proc/$pid/exe | cut -c1-32)"; echo "proof_binlink $(readlink -f $S/bin/alpha-tk044k1-new)"; tr '\0' '\n' < /proc/$pid/environ | grep '^ALPHA_' | sed 's/^/proof_env /'; } > $R/proof.txt 2>&1; break;; esac; done ) & PROOFPID=$!
pfday() { d=$1; if [ -n "$PFLIST" ]; then awk -v d=$d '$2==d{print $1}' $PFLIST | sort -u; else awk -F, -v d=$d 'NR==1{for(i=1;i<=NF;i++){if($i=="sym")a=i;if($i=="day")b=i};next} $b==d{print $a}' /data/tk044/final3/verdict.csv | sort -u; fi | while read -r sy; do ls $S/study/root-$d/$sy-$d.binlog* 2>/dev/null; done | while read -r f; do readlink -f "$f"; done > $SH/pf-$d.txt
  grep '^/alpha-sda/' $SH/pf-$d.txt | sort | xargs -d '
' -n 8 cat > /dev/null 2>> $R/prefetch.err & grep -v '^/alpha-sda/' $SH/pf-$d.txt | sort | xargs -d '
' -n 8 cat > /dev/null 2>> $R/prefetch.err & wait; }
PF=${PREFETCH:-0}
warm_day() { d=$1; { find -L $S/study/approaches/D20/$d -type f 2>/dev/null | { [ -n "$WARM_NOCSV" ] && grep -v "/approaches-.*[.]csv\$" || cat; }; echo $S/study/regime/$d.csv; find -L $S/study/root-$d -type f ! -name '*.binlog*' 2>/dev/null
  [ -z "$WARM_ONCE" ] || { find -L $S/study/sigma240 -type f 2>/dev/null; echo /data/tk044/final3/verdict.csv; }; } | xargs -d '
' cat > /dev/null 2>> $R/prewarm.err
  find -L $S/study/root-$d -type f -name '*.binlog*' 2>/dev/null | xargs -d '
' -n 16 head -c 65536 > /dev/null 2>> $R/prewarm.err; }
if [ "${PREWARM_SMALL:-0}" = 1 ]; then
WARM_ONCE=1 warm_day $(echo $days | cut -d' ' -f1); echo "$(echo $days | cut -d' ' -f1) $(date +%s.%N)" >> $R/prewarm.txt
( i=0; for d in $days; do
    if [ $i -gt 0 ]; then
      while [ $(ls $S/done-* 2>/dev/null | wc -l) -lt $(( (i-${PREWARM_AHEAD:-2})*G )) ]; do sleep 1; done
      warm_day $d; echo "$d $(date +%s.%N)" >> $R/prewarm.txt; fi; i=$((i+1)); done ) &
PWPID=$!
fi
if [ "$PF" -gt 0 ]; then
( i=0; for d in $days; do
    while [ $(ls $S/done-* 2>/dev/null | wc -l) -lt $(( (i-PF)*G )) ]; do sleep 2; done
    pfday $d
    echo "$d $(date +%s.%N)" >> $R/prefetch.txt; i=$((i+1)); done ) &
PFPID=$!
fi
set -- $days; Q=$S/queue.txt; : > $Q
if [ "${QALT:-0}" = 1 ]; then while [ $# -gt 0 ]; do a=$1; b=${2:-}; shift; [ $# -gt 0 ] && shift; for ((k=0;k<G;k++)); do echo "$a $k" >> $Q; [ -n "$b" ] && echo "$b $k" >> $Q; done; done
else for d in $days; do for ((k=0;k<G;k++)); do echo "$d $k" >> $Q; done; done; fi
export MEMGATE_KB=$(( ${MEMGATE_GB:-0}*1048576 )) GATELOCK=$S/gate.lock
xargs -P $P -L1 bash -c 'if [ "$MEMGATE_KB" -gt 0 ]; then flock $GATELOCK bash /data/tk048/tk048-gate.sh; fi; bash /data/tk048/tk048-orch-job3.sh '$S' '$R' $0 $1 '$G < $Q
[ -n "$PFPID" ] && kill $PFPID 2>/dev/null; [ -n "$PWPID" ] && kill $PWPID 2>/dev/null; kill $PROOFPID 2>/dev/null; kill -TERM $PERFPID 2>/dev/null; pkill -fx 'sleep 100000'; sleep 0.3; us1=$(awk '/^user_usec/{print $2}' $CG/cpu.stat); sy1=$(awk '/^system_usec/{print $2}' $CG/cpu.stat); kill $SMP 2>/dev/null; t1=$(date +%s.%N); c1=$(systemctl show -p CPUUsageNSec --value $U); cpu1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd1=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra1=$(awk '$3=="sda"{print $6}' /proc/diskstats)
python3 - <<PY > $R/metrics.txt
a="$cpu0".split(); b="$cpu1".split(); w=$t1-$t0
f=sorted(float(l.split()[1]) for l in open("$R/finish.txt"))
print("wall_s",round(w,1)); print("iowait_pct",round(100*(float(b[1])-float(a[1]))/(float(b[0])-float(a[0])),1))
print("cpu_busy_machine_s",round((float(b[2])-float(a[2]))/100,1)); print("cpu_unit_s",round(($c1-$c0)/1e9,1)); print("core_util_pct_of_15",round(100*($c1-$c0)/1e9/(15*w),1))
print("disk_MBps_sdb",round(($rd1-$rd0)*512/1e6/w,1)); print("disk_MBps_sda",round(($ra1-$ra0)*512/1e6/w,1))
bz=(float(b[2])-float(a[2]))/100; un=($c1-$c0)/1e9; ip=100*(bz-un)/un
print("interference_pct",round(ip,1)); print("INVALID_INTERFERENCE" if ip>5 else "interference_ok")
print("tail_s",round(f[-1]-f[-2],1) if len(f)>1 else 0, "first_finish_s",round(f[0]-$t0,1), "G",$G)
PY
bash /data/tk048/tk048-tail.sh tail $R $U
echo "cached0_MB $((cached0/1024)) cached1_MB $(( $(awk '/^Cached/{print $2}' /proc/meminfo)/1024 )) read_GB_sdb $(( (rd1-rd0)*512/1000000000 )) read_GB_sda $(( (ra1-ra0)*512/1000000000 )) util_sdb_pct $(( ($(awk '$3=="sdb"{print $13}' /proc/diskstats)-it0)*100/$(echo "($t1-$t0)*1000/1" | bc | cut -d. -f1) )) util_sda_pct $(( ($(awk '$3=="sda"{print $13}' /proc/diskstats)-ia0)*100/$(echo "($t1-$t0)*1000/1" | bc | cut -d. -f1) )) NODROP ${NODROP:-0}" >> $R/metrics.txt
echo "cpu_user_s $(( (us1-us0)/1000000 )) cpu_sys_s $(( (sy1-sy0)/1000000 )) P $P" >> $R/metrics.txt
[ -e $R/proof.txt ] && cat $R/proof.txt >> $R/metrics.txt
python3 - $R/perf.txt >> $R/metrics.txt <<'PY'
import sys
d={}
for l in open(sys.argv[1]):
    f=l.strip().split(',')
    if len(f)>3 and f[1].isdigit(): d[f[3]]=d.get(f[3],0)+float(f[1])
if 'instructions' in d and 'cycles' in d:
    print('perf_instructions_G',round(d['instructions']/1e9,1),'cycles_G',round(d['cycles']/1e9,1),'IPC',round(d['instructions']/d['cycles'],3),'cache_misses_G',round(d.get('cache-misses',0)/1e9,2),'cache_refs_G',round(d.get('cache-references',0)/1e9,2))
PY
[ -e $R/fail.txt ] && cat $R/fail.txt >> $R/metrics.txt
python3 - $R >> $R/metrics.txt <<"PY"
import sys,glob,os
r=[int(open(f).read().split()[-1]) for f in glob.glob(sys.argv[1]+"/t-*.txt")]
print("maxrss_max_MB",round(max(r)/1024),"maxrss_mean_MB",round(sum(r)/len(r)/1024),"units",len(r),"events_wide","1" if os.environ.get("EVENTS_WIDE") else "0")
PY
[ -n "$BENCH_IOSTATE" ] && python3 /data/tk052/io-acct.py report $BENCH_IOSTATE >> $R/metrics.txt
rm $S/b5; cp -a $SH/b5 $S/b5 && rm -rf $SH; bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || bad=$((bad+1)); done < <(find . -type f | grep -v "/\." )
echo "gate files $n diff $bad" >> $R/metrics.txt; touch $R/.done
