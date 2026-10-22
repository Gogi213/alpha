#!/usr/bin/env bash
# TK-289 (В-21, П-18 F3 оси 5): ENTRY_TTL_SECS +8 точек F3 (только добавление) -> PGO use профилем tk289 (горячий код не менялся; pgo-raw из tk0289b)
# -> mechanics4.json от нового бинарника -> гейт байт-в-байт против эталона g1 (A1 = tk279-pgo) -> F3 оси 5 (8 кл. x 251 сут., 15 яд.) -> psim + closes с монетой.
# Маркеры /data/tk0289c/chain4.{ok,fail}; журнал chain4.log.
set -u
TAG=tk0289c; SRC=/data/tk0289/src-pgo3; W=/data/tk0289c; L=$W/chain4.log; B=/data/tk0272/build-pgo.sh
rm -f $W/chain4.ok $W/chain4.fail
waitrc() { [ -n "$1" ] || return 9; while [ ! -f /data/sched/rc/$1 ]; do sleep 20; done; return "$(cat /data/sched/rc/$1)"; }
fail() { echo "FAIL $1" | tee -a $L; touch $W/chain4.fail; exit 1; }
sub() { python3 /data/sched/alsched.py submit "$@" 2>&1 | tee -a $L | grep -o '[0-9]\{13\}' | head -1; }
u=$(sub --name tk289-pgo4 --kind build --cores 3 --mem 12 --max-runtime 180m --est 30m --adhoc "TK-289 PGO use: сетка ttl F3 оси 5 (В-74), профиль tk289, В-21" --reason "сборка PGO, не проход счёта" --cwd $W -- env FEATURES=r2 bash $B $TAG $SRC use)
echo "pgo4 $u" >> $L; waitrc "$u" || fail pgo
/opt/alpha-compute/bin/alpha-$TAG-pgo lob mechanics --json > $W/mechanics4.json 2>> $L || fail mechanics
md5sum $W/mechanics4.json >> $L
g=$(sub --name tk289-gate4 --kind gate --cores 3 --mem 12 --max-runtime 120m --est 15m --adhoc "TK-289 гейт новый pgo (ttl F3) = эталон g1" --reason "гейт, 1 сутки 10 клеток" --cwd $W -- bash /data/tk0289/gate-pgo4.sh)
echo "gate4 $g" >> $L; waitrc "$g" || fail gate
python3 /data/registry/guard.py plan --steps /data/tk0289/steps-f3ttl.tsv >> $L 2>&1
f=$(sub --name tk289-f3ttl --cores 15 --mem 40 --max-runtime 5h --est 10m --vypiska /data/tk0289/steps-f3ttl.tsv --cwd /data/tk0289 -- bash /data/tk0289/f3ttl.sh)
echo "f3ttl $f" >> $L; waitrc "$f" || fail f3ttl
python3 /data/registry/guard.py plan --steps /data/tk0289/steps-f3ttl-psim.tsv >> $L 2>&1
p=$(sub --name tk289-f3ttl-psim --cores 15 --mem 40 --max-runtime 3h --est 10m --vypiska /data/tk0289/steps-f3ttl-psim.tsv --cwd /data/tk0289 -- bash /data/tk0289/f3ttl-psim.sh)
echo "psim $p" >> $L; waitrc "$p" || fail psim
touch $W/chain4.ok; echo OK >> $L
