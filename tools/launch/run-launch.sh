#!/usr/bin/env bash
# TK-241: раннер формы запуска (alsched submit … -- bash run-launch.sh <каталог запуска>): сутки выбранных месяцев × клетки через day-cells.sh.
#   <каталог запуска>/plan/L-<ГГГГ-ММ>.cells|.args|.bin + months.txt пишет launch.py (prepare_plan); результат — <каталог>/out/run-<мес>-<сутки>/
#   env: BIN (бинарь из config.json), SCRIPTS (каталог с day-cells.sh, day-order.sh, merge-packs.py; config.json scripts_dir), NX (исполнители), GT
# Параметры day-cells.sh — как у прохода TK-231 (run-all.sh): PLAN, P, O, BIN, GT, TP; суточный claim: второй запуск берёт только неразобранное.
set -uo pipefail
RUN=$1; S=${SCRIPTS:-/data/tk0231}; NX=${NX:-12}; B=${B1:-/data/tk0115/delta/b1-symdays.csv}
export PLAN=L P=$RUN/plan O=$RUN/out BIN GT=${GT:-1} TP=1
[ -s "$P/months.txt" ] || { echo "нет $P/months.txt" >&2; exit 2; }
D=$RUN/days.txt
if [ ! -s "$D" ]; then
  mons=(jan feb mar apr may jun jul aug sep)
  while read -r ym; do m=${mons[10#${ym:5:2}-1]}; for d in $(bash "$S/day-order.sh" "$B" "$m"); do echo "$m $d"; done; done < "$P/months.txt" > "$D.tmp" && mv "$D.tmp" "$D"
fi
mkdir -p "$O"
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$S" < "$D"
RC=$?
# после счёта (В-7): psim -> оверлей сервера; без нейросетей (В-6); ошибка — <RUN>/postload.log (красная строка на вкладке «Запуск»)
H=$(cd "$(dirname "$0")" && pwd)
bash "$H/psim-launch.sh" "$RUN" >> "$RUN/postload.out" 2>&1 && python3 "$H/postload.py" "$RUN" >> "$RUN/postload.out" 2>&1 \
  || { [ -s "$RUN/postload.log" ] || echo "$(basename "$RUN"): psim/postload rc≠0, см. postload.out" > "$RUN/postload.log"; }
exit $RC
