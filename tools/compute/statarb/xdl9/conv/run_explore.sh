#!/bin/bash
# TK-493, П-39: запуск прохода поиска юнитом systemd --user на Deck. Использование:
#   bash run_explore.sh <метка> <univ> [аргументы conv_explore run ...]    (логи: ~/xdl9/c2x/logs/<метка>.log; код выхода в <метка>.exit)
# Переменные: TH (6 потоков), MEMMAX (12G), NICE (10), CODE (~/xdl9/c2x/code), OUT (~/xdl9/c2x).
set -u
LABEL=$1; UNIV=$2; shift 2
TH=${TH:-6}; MEMMAX=${MEMMAX:-12G}; NICE=${NICE:-10}
CODE=${CODE:-$HOME/xdl9/c2x/code}; OUT=${OUT:-$HOME/xdl9/c2x}
mkdir -p "$OUT/logs"; rm -f "$OUT/logs/$LABEL.exit"
free_gb=$(df -BG --output=avail /home | tail -1 | tr -dc 0-9)
echo "свободно на /home: ${free_gb} ГБ" | tee "$OUT/logs/$LABEL.preflight"
cat > "$OUT/logs/$LABEL.inner.sh" <<INNER
#!/bin/bash
cd $CODE/tools/compute/statarb/xdl9/conv
echo "START \$(date -u +%FT%TZ) $LABEL"
PYTHONDONTWRITEBYTECODE=1 $HOME/xdl9/venv/bin/python conv_explore.py run --univ $UNIV --tag $LABEL --threads $TH --out $OUT --code-root $CODE $@
rc=\$?
echo "EXIT=\$rc \$(date -u +%FT%TZ)"
echo \$rc > $OUT/logs/$LABEL.exit
INNER
systemd-run --user --unit="x-$LABEL" --collect -p MemoryMax=$MEMMAX --nice=$NICE bash -c "bash $OUT/logs/$LABEL.inner.sh > $OUT/logs/$LABEL.log 2>&1"
echo "запущено юнит x-$LABEL (потоков $TH, MemoryMax $MEMMAX, nice $NICE); лог $OUT/logs/$LABEL.log"
