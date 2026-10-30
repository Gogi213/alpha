#!/bin/bash
# TK-464: проверка ошибки «пары, склеенные xargs -L1 со служебной строкой». Падает на прежнем конвейере bench3
# (ls | sed | awk без фильтра *.csv.gz), проходит на pairs_make из tk463-lib.sh.
set -u
D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
H=$(dirname "$0")
export D; mkdir -p "$D/ev"
for c in 0GUSDT DOTUSDT ETHFIUSDT LTCUSDT PLUMEUSDT; do for tf in 60 240 1440; do : > "$D/ev/$c-$tf.csv.gz"; done; done
for f in DONE err.log log.txt plan.txt; do : > "$D/ev/$f"; done
# прежний конвейер: число вызовов job и пропавшие пары
ls "$D/ev" | sed 's/\.csv\.gz$//' | awk -F- '{print $1, $2}' > "$D/old.txt"
old_merged=$(xargs -L1 bash -c 'echo "$0 $1 ${2:-}"' < "$D/old.txt" | awk 'NF==3' | wc -l)
echo "прежний: строк $(wc -l < "$D/old.txt") (ожидалось 15), склеенных вызовов xargs $old_merged (ожидалось 0)"
source "$H/tk463-lib.sh"
pairs_make "$D/ev" "$D/new.txt" || { echo "FAIL: pairs_make"; exit 1; }
new_calls=$(xargs -L1 bash -c 'echo "$0 $1"' < "$D/new.txt" | wc -l)
echo "pairs_make: строк $(wc -l < "$D/new.txt"), вызовов xargs $new_calls"
[ "$new_calls" = 15 ] || { echo "FAIL: ожидалось 15"; exit 1; }
# полнота: job пишет вывод; одна пара падает -> check_complete должен отказать
job() { [ "$1-$2" = "LTCUSDT-60" ] && return 3; echo x > "$D/out/$1-$2.csv.gz"; }
mkdir -p "$D/out"; run_pairs "$D/new.txt" "$D/rc" 4
check_complete "$D/new.txt" "$D/rc" csv.gz "$D/out" && { echo "FAIL: упавшая пара не замечена"; exit 1; }
rm -f "$D/out/"* ; job() { echo x > "$D/out/$1-$2.csv.gz"; }; run_pairs "$D/new.txt" "$D/rc" 4
check_complete "$D/new.txt" "$D/rc" csv.gz "$D/out" || { echo "FAIL: полный набор отклонён"; exit 1; }
[ "$old_merged" = 0 ] || echo "прежний конвейер теряет пары: $old_merged склеено — воспроизведено"
grep -q 'pairs_make' "$H/tk463-bench3.sh" || { echo "FAIL: tk463-bench3.sh строит пары без pairs_make"; exit 1; }
echo OK
