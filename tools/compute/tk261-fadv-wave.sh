#!/bin/bash
# TK-261 Ш2.3: волна января 01–15 (p177/grp16q, PREFETCH=0, G=8, P=20) на бинарнике с ALPHA_CARRY_FADV_RANDOM; ABBA (A=выкл., B=вкл.) в одном окне alsched wave.
# Подача (на calc): systemd-run --unit tk261-abba-fadv --collect python3 /data/sched/alsched.py wave --max-runtime 40m bash /data/tk0261/tk261-fadv-wave.sh abba <бинарник из /opt/alpha-compute/bin>
# Одна волна: tk261-fadv-wave.sh <метка> <бинарник> <0|1>. Снимки → /data/tk0261/<метка>/; метрики → /data/tk048/orch-t261<метка>.out/metrics.txt; по завершении ABBA — /data/tk0261/abba-fadv.done.
if [ "$1" = abba ]; then D=/data/tk0261; for x in 1:0 2:1 3:1 4:0; do bash $D/tk261-fadv-wave.sh fadv${x%%:*} $2 ${x##*:}; done; date -Is > $D/abba-fadv.done; exit; fi
lab=$1; BINN=$2; FADV=$3; pre=; post=; PFV=0; O=/data/tk0261/$lab; mkdir -p $O; exec > $O/run.log 2>&1
snap() { { echo "ts $(date +%s.%N)"; grep '^cpu ' /proc/stat; grep -E '^(thp_fault_alloc|thp_fault_fallback|compact_stall|compact_fail|pswpin|pswpout|pgmajfault|allocstall_movable) ' /proc/vmstat; grep -E '^(MemAvailable|Cached|Shmem|SwapFree):' /proc/meminfo; awk '$3=="sda"||$3=="sdb"{print $3,"rd_sect",$6,"io_ms",$13}' /proc/diskstats; } > $O/$1.txt; }
trap '[ -z "$post" ] || bash $post; kill $FQ 2>/dev/null; date -Is > $O/done' EXIT
[ -z "$pre" ] || bash $pre
( while sleep 5; do echo "$(date +%s) $(grep MHz /proc/cpuinfo | awk '{s+=$4;n++} END{printf "%.0f",s/n}')"; done > $O/mhz.tsv ) & FQ=$!
snap before
D=$(seq -s, -f "2026-01-%02g" 1 15)
t0=$(date +%s)
systemd-run --wait --collect --unit tk261w-$lab -p CPUQuota=1500% \
 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=WARM_BG=1 --setenv=PREFETCH=$PFV --setenv=ALPHA_CARRY_FADV_RANDOM=$FADV --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=EVENTS_WIDE=1 \
 bash /data/tk048/tk048-orch-grp16q.sh t261$lab $BINN "$D" 8 20 > $O/orch.out 2>&1
echo "wall_s $(( $(date +%s)-t0 ))" > $O/summary.txt
snap after
python3 - $O >> $O/summary.txt <<'PY'
import sys
o=sys.argv[1]
def rd(n):
    d={}
    for l in open(f'{o}/{n}.txt'):
        p=l.split()
        if p[0]=='cpu': d['cpu']=list(map(int,p[1:]))
        elif p[0] in('sda','sdb'): d[p[0]+'_rd_MB']=int(p[2])*512/1e6; d[p[0]+'_io_ms']=int(p[4])
        else:
            try: d[p[0].rstrip(':')]=float(p[1])
            except ValueError: pass
    return d
a,b=rd('before'),rd('after')
c=[y-x for x,y in zip(a['cpu'],b['cpu'])]; t=sum(c)
print('cpu_share_pct user %.1f nice %.1f sys %.1f idle %.1f iowait %.1f irq %.1f softirq %.1f'%tuple(100*c[i]/t for i in (0,1,2,3,4,5,6)))
for k in a:
    if k!='cpu' and k!='ts': print(k,'before',a[k],'after',b[k],'delta',round(b[k]-a[k],1))
m=[int(l.split()[1]) for l in open(f'{o}/mhz.tsv') if len(l.split())==2]
print('mhz_avg %.0f min %d max %d n %d'%(sum(m)/len(m),min(m),max(m),len(m)))
PY
