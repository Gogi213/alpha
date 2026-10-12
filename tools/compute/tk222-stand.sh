#!/bin/bash
# TK-222 стенд (A,B: SCHED_PACK=claim — guard_load выключен, видна метка; C: fact — оттайка): второй экземпляр демона (свой SCHED_DIR, SCHED_PAT только stand222) — заморозка → рестарт демона → метка hot → оттайка.
# запуск на calc из каталога с alsched.py и sched_cfg.py: bash tk222-stand.sh
set -u
export SCHED_DIR=/tmp/tk222/sched SCHED_PAT='tk0s-stand222*' SCHED_PACK=claim SCHED_TICK=2 SCHED_RUNS_DIR=/tmp/tk222/runs SCHED_RESERVE_CORES=0
rm -rf /tmp/tk222; mkdir -p $SCHED_DIR $SCHED_RUNS_DIR
A="python3 $(pwd)/alsched.py"
ENVS=(-E SCHED_DIR -E SCHED_PAT -E SCHED_PACK -E SCHED_TICK -E SCHED_RUNS_DIR -E SCHED_RESERVE_CORES)
startd() { systemd-run --unit=$1 --collect "${ENVS[@]}" ${2:+-E SCHED_PACK=fact} $A daemon >/dev/null 2>&1; }
J() { cat $SCHED_DIR/jobs/*.json | python3 -c "import sys,json; j=json.load(sys.stdin); print({k:j.get(k) for k in ('state','frozen_for','active_s')})"; }
FS() { systemctl show -p FreezerState --value $U; }
echo "== $(date +%T) host load: $(cut -d' ' -f1-3 /proc/loadavg)"
startd tk222-d1; sleep 3
$A submit --name stand222 --cls prod --cores 1 --mem 1 --max-runtime 10m -- sleep 600; sleep 8
U=$(systemctl list-units --plain --no-legend 'tk0s-stand222*' | awk '{print $1}' | head -1)
echo "== unit=$U state: $(J) freezer=$(FS)"
echo "== A: заморозка без метки (как после потери frozen_for), демон убит"
systemctl stop tk222-d1; systemctl freeze $U; sleep 1
echo "   json до рестарта: $(J) freezer=$(FS)"
startd tk222-d2; sleep 6
echo "   == после рестарта: json=$(J) freezer=$(FS)"
echo "== B: метка переживает второй рестарт"
systemctl stop tk222-d2; startd tk222-d3; sleep 6
echo "   json=$(J) freezer=$(FS)"
echo "== C: демон в режиме fact (guard_load) — оттайка метки hot по спаду загрузки (≤ 150 с)"
systemctl stop tk222-d3; startd tk222-d4 fact; sleep 2
for i in $(seq 1 30); do sleep 5; f=$(FS); [ "$f" != frozen ] && break; done
echo "   $(date +%T) freezer=$f json=$(J) load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "== alerts:"; tail -3 $SCHED_DIR/alerts.log 2>/dev/null
systemctl stop tk222-d4 tk0s-stand222* 2>/dev/null; systemctl thaw $U 2>/dev/null; systemctl stop $U 2>/dev/null
echo "== конец $(date +%T)"
