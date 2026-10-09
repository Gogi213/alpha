#!/bin/bash
# tk048-chain9.sh: после исправления подмены бинарника (bin из эталонного каталога перекрывал ссылку — все прежние orch-волны шли на alpha-tk044k1-new).
# QUICK5, подогрев везде: tk048rd; b4-pgo+флаги; он же без сброса кэша (q5hot, «диск = 0»); b6+флаги; связка целиком b6 (15 суток); P=11 и 8 на b6.
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch; D15=$(seq -s, -f '2026-01-%02g' 1 15)
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
F=(--setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1)
ENVX=(--setenv=PREWARM_SMALL=1); run bash $T-grp.sh q5rd alpha-tk048rd $Q 8 15
ENVX=("${F[@]}"); run bash $T-grp.sh q5pgo alpha-b4-pgo $Q 8 15
ENVX=("${F[@]}" --setenv=NODROP=1); run bash $T-grp.sh q5hot alpha-b4-pgo $Q 8 15
ENVX=("${F[@]}"); run bash $T-grp.sh q5b6 alpha-b6 $Q 8 15
run bash $T-grp.sh q15b6 alpha-b6 $D15 8 15
run bash $T-grp.sh q5b6p11 alpha-b6 $Q 8 11
run bash $T-grp.sh q5b6p8 alpha-b6 $Q 8 8
