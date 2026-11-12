#!/usr/bin/env bash
# TK-366 (G-016): полный проход — все монеты >$50k, июль–сентябрь 2026: спот затем фьюч, сырьё удаляется сразу, бинлоги на sda5.
#   tk366-pass.sh <шард i> <шардов K>    env: JOBS(умолч. 4) BIN
# Шард i берёт строки NR%K==i обоих списков (/srv/alpha-sda/tk366/{spot,fut}-todo.txt); готовое пропускается — повторный запуск безопасен.
# Выход: /srv/alpha-sda/tk366/bin-{fut,spot}/e-YYYY-MM/SYM-DAY.binlog ; журнал log/import-{fut,spot}-i.tsv (рынок монета сутки статус …)
i=${1:?шард}; K=${2:?шардов}; D=/srv/alpha-sda/tk366
export BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo} INSTR=$D/instruments.csv TMP=$D/tmp/s$i JOBS=${JOBS:-4}
mkdir -p "$D/log" "$TMP"
for mk in spot fut; do
  export ROOT=$D/bin-$mk LOG=$D/log/import-$mk-$i.tsv
  awk -v i="$i" -v k="$K" 'NR%k==i' "$D/$mk-todo.txt" > "$D/log/list-$mk-$i.txt"
  bash "$D/tk366-run.sh" "$mk" "$D/log/list-$mk-$i.txt"
done
touch "$D/log/done-$i"
