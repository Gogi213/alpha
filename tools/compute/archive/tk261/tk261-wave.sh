#!/bin/bash
# TK-261: одна волна января 01–15 (конфиг p177/TK-178: grp16q, PREFETCH=5, G=8, P=20) внутри окна alsched wave.
# Подача (на calc): systemd-run --unit tk261-<метка> --collect python3 /data/sched/alsched.py wave --max-runtime 30m bash /data/tk0261/tk261-wave.sh <метка> [пре] [пост]
# <4-й> — значение PREFETCH (умолч. 5). <пре>/<пост> — скрипты настройки ОС и её возврата; <пост> идёт всегда (trap). Снимки до/после → /data/tk0261/<метка>/; метрики волны → /data/tk048/orch-<метка>.out/metrics.txt
lab=$1; pre=${2:-}; post=${3:-}; PFV=${4:-5}; O=/data/tk0261/$lab; mkdir -p $O; exec > $O/run.log 2>&1
snap() { { echo "ts $(date +%s.%N)"; grep '^cpu ' /proc/stat; grep -E '^(thp_fault_alloc|thp_fault_fallback|compact_stall|compact_fail|pswpin|pswpout|pgmajfault|allocstall_movable) ' /proc/vmstat; grep -E '^(MemAvailable|Cached|Shmem|SwapFree):' /proc/meminfo; awk '$3=="sda"||$3=="sdb"{print $3,"rd_sect",$6,"io_ms",$13}' /proc/diskstats; } > $O/$1.txt; }
trap '[ -z "$post" ] || bash $post; kill $FQ 2>/dev/null; date -Is > $O/done' EXIT
[ -z "$pre" ] || bash $pre
( while sleep 5; do echo "$(date +%s) $(grep MHz /proc/cpuinfo | awk '{s+=$4;n++} END{printf "%.0f",s/n}')"; done > $O/mhz.tsv ) & FQ=$!
snap before
D=$(seq -s, -f "2026-01-%02g" 1 15)
t0=$(date +%s)
systemd-run --wait --collect --unit tk261w-$lab -p CPUQuota=1500% \
 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=WARM_BG=1 --setenv=PREFETCH=$PFV --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=EVENTS_WIDE=1 \
 bash /data/tk048/tk048-orch-grp16q.sh t261$lab alpha-tk174b-pgo "$D" 8 20 > $O/orch.out 2>&1
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
