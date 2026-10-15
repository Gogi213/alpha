#!/bin/bash
# TK-251 П4 (В-12/G-002): шаг 2 запуска формы — R1 (маршруты r1 и r1x): клетки месяца -> psim -> <RUN>/r1/ps/closes-cap{0,3}-<jan…sep>.json + psim-r1.done.
#   r1: r1cell.py apply по <RUN>/plan/L-<ГГГГ-ММ>.r1spec (отбор по B1-пулу, исходы из rounds.csv); r1x: готовые клетки $R1X_CELLS/<мес>/<хеш>.csv копируются как есть (В-213, не пересчитываются).
#   psim — канонический p12-r1-psim.py с подменой O/CELLS и фильтром R1KEEP (так считали TK-250/TK-251; эквивалентность r1ext и r1x принята). Ошибка -> <RUN>/r1/r1-fail.txt.
#   r1-launch.sh <RUN>; env R1CELL POOL R1X_CELLS PSIM_SRC STUDY PM (параллельных месяцев, 3). Нейросетей не зовёт (В-6).
set -u
RUN=$1; P=$RUN/plan; O=$RUN/r1; H=$(cd "$(dirname "$0")" && pwd)
R1CELL=${R1CELL:-$H/../compute/r1cell.py}; POOL=${POOL:-/data/tk064/pool}; X=${R1X_CELLS:-/data/tk202/r1x/cells}
SRC=${PSIM_SRC:-/data/tk063r1/p12-r1-psim.py}; STUDY=${STUDY:-/data/p12r2/v-b/study}; PM=${PM:-3}
mkdir -p "$O/cells" "$O/ps/b"; cd "$O" || exit 2; rm -f psim-r1.done r1-fail.txt
[ -s "$P/r1months.txt" ] || { echo "нет $P/r1months.txt" >> r1-fail.txt; exit 3; }
ln -sfn "$STUDY" ps/b/study
python3 - "$SRC" "$O" <<'PY' || { echo "подмена p12-r1-psim.py" >> r1-fail.txt; exit 3; }
import sys
s = open(sys.argv[1], encoding="utf-8").read()
s = s.replace('O = "/data/tk063r1"', f'O = "{sys.argv[2]}/ps"').replace('CELLS = "/data/tk064/r1/cells"', f'CELLS = "{sys.argv[2]}/cells"')
a = "        cell = os.path.basename(cf)[:-4]\n"
assert a in s
s = s.replace(a, a + "        if cell not in os.environ['R1KEEP'].split(','):\n            continue\n")
open(sys.argv[2] + "/ps.py", "w", encoding="utf-8").write(s)
PY
one() { ym=$1; mons=(jan feb mar apr may jun jul aug sep); mon=${mons[10#${ym:5:2}-1]}; mkdir -p "$O/cells/$mon"
  if [ "$(wc -l < "$P/L-$ym.r1spec" 2>/dev/null || echo 0)" -gt 1 ]; then
    python3 "$R1CELL" apply --spec "$P/L-$ym.r1spec" --pool "$POOL" --out "$O/apply-$mon" --months "$mon" > "$O/apply-$mon.log" 2>&1 \
      && cp "$O"/apply-$mon/cells/$mon/*.csv "$O/cells/$mon/" || { echo "apply $mon" >> "$O/r1-fail.txt"; return; }
  fi
  [ -e "$P/L-$ym.r1x" ] && while read -r h; do
    cp "$X/$mon/$h.csv" "$O/cells/$mon/" || echo "нет готовой клетки $mon/$h" >> "$O/r1-fail.txt"
  done < "$P/L-$ym.r1x"
  R1KEEP=$(ls "$O/cells/$mon" | sed 's/\.csv$//' | paste -sd,) python3 "$O/ps.py" "$mon" > "$O/ps/run-$mon.log" 2>&1 || echo "psim $mon" >> "$O/r1-fail.txt"
  [ -e "$O/ps/psim-$mon.done" ] && [ ! -e "$O/ps/fail-$mon.txt" ] || echo "psim $mon не дошёл" >> "$O/r1-fail.txt"; }
export -f one; export O P R1CELL POOL X   # массив mons не экспортируется в xargs-ребёнка — задан внутри one()
xargs -P "$PM" -I{} bash -c 'one {}' < "$P/r1months.txt"
[ -s r1-fail.txt ] && exit 5
touch psim-r1.done
