#!/bin/bash
# TK-223 стенд: второй экземпляр демона (свой SCHED_DIR, SCHED_PAT только stand223). Запуск на calc из каталога с alsched.py: bash tk223-stand.sh
set -u
export SCHED_DIR=/tmp/tk223/sched SCHED_PAT='tk0s-stand223*' SCHED_PACK=claim SCHED_TICK=2 SCHED_RUNS_DIR=/tmp/tk223/runs SCHED_RESERVE_CORES=3
rm -rf /tmp/tk223; mkdir -p $SCHED_DIR $SCHED_RUNS_DIR
mkdir -p /tmp/tk223/stubreg; printf 'def check(cls, argv, *a):
    return 0, dict(pending="/tmp/tk223/pend", argv=argv)
if __name__ == "__main__":
    pass
' > /tmp/tk223/stubreg/guard.py   # реестр боевой не трогаем
export REG_TOOLS=/tmp/tk223/stubreg
D=$(pwd); A="python3 $D/alsched.py"
ENVS=(-E SCHED_DIR -E SCHED_PAT -E SCHED_PACK -E SCHED_TICK -E SCHED_RUNS_DIR -E SCHED_RESERVE_CORES)
echo "== $(date +%T) load: $(cut -d' ' -f1-3 /proc/loadavg)"
systemd-run --unit=tk223-d1 --collect "${ENVS[@]}" $A daemon >/dev/null 2>&1; sleep 3
echo "== п.4 отмена из очереди пишет rc"
B=$($A submit --name stand223-blk --cls prod --cores 13 --mem 1 --max-runtime 10m -- sleep 600 | tail -1); sleep 6
Q=$($A submit --name stand223-q --cls prod --cores 8 --mem 1 --max-runtime 10m -- sleep 5 | tail -1); sleep 6
$A cancel $Q "стенд tk223"; sleep 8
echo "   rc/$Q = $(cat $SCHED_DIR/rc/$Q 2>&1)"; $A ps | grep stand223 | cut -c1-90
echo "== п.5 submit gate без --kind"
$A submit --name stand223-gate1 --cls prod --cores 1 --mem 1 --max-runtime 1m -- true 2>&1 | head -1
echo "== п.1/п.6 оттайка по замеру + RuntimeMaxSec после оттайки"
$A cancel $Q >/dev/null; sleep 1
G=$($A submit --name stand223-g --cls prod --cores 1 --mem 1 --max-runtime 10m --kind gate -- sleep 600 | tail -1); sleep 6
systemctl stop tk223-d1; sleep 1
U=$(systemctl list-units --plain --no-legend 'tk0s-stand223-g*' | awk '{print $1}' | head -1)
echo "   unit=$U RuntimeMaxUSec=$(systemctl show -p RuntimeMaxUSec --value $U)"
systemctl freeze $U
python3 - <<PY
import sys,time; sys.path.insert(0,"$D")
import alsched as S
be=S.SystemdBackend(); core=S.Core(be)
j=[x for x in S.load_all() if x["id"]=="$G"][0]
now=time.time()
j.update(state="running",frozen_for="hot",frozen_need=1,frozen_t=now-1000,cores=8,cpus=list(range(8)))   # заявка 8, замер 1; заморожено 1000 с
core.add(j)
core.alert_frozen(now); core.guard_load(now, 0.8, True)
print("   frozen_for после guard_load:", j.get("frozen_for"))
PY
echo "   freezer=$(systemctl show -p FreezerState --value $U) RuntimeMaxUSec=$(systemctl show -p RuntimeMaxUSec --value $U)"
echo "== alerts:"; grep -a "stand223" $SCHED_DIR/alerts.log
systemctl thaw $U 2>/dev/null; systemctl stop tk223-d1 'tk0s-stand223*' 2>/dev/null
echo "== конец $(date +%T)"
