#!/usr/bin/env bash
# TK-393 (G-016) докачка 3: Solidly-свопы (Aerodrome v2 на Base, Velodrome v2 на Optimism) — в dl2 курсор скопирован
# «закончен» (начало>конец) → 0 свопов. Здесь курсор не копируется: окно блоков считается по T0..T1 (бинпоиск в скрипте).
# Выход /data/tk0393/sqd3/. Нужны файлы dl2: pools-solidly.csv, tk393-sqd-solidly.py, tk393-sqd-parse2.py.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd3; T0=1792022400; T1=1792713599
mkdir -p "$R"; cd "$S" || exit 1
python3 -u tk393-sqd-solidly.py base-mainnet pools-solidly.csv base "$R" $T0 $T1 > "$R/base.log" 2>&1 &
python3 -u tk393-sqd-solidly.py optimism-mainnet pools-solidly.csv optimism "$R" $T0 $T1 > "$R/optimism.log" 2>&1 &
wait
for c in base optimism; do [ -f "$R/$c/DONE.json" ] && python3 tk393-sqd-parse2.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1; done
echo '{"done": true}' > "$R/ALLDONE.json"
