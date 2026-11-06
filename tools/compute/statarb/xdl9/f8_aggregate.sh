#!/usr/bin/env bash
# TK-493 w5 F: вся агрегация F8 одной командой (Deck, ~/xdl9/venv): cells (ряды, статусы), slices, controls, jumps, точный фандинг D2, черновик отчёта F10.
# Не вычисляет ничего сверх f8_agg.py / d2_exact_funding.py / f8_report.py; читает выходы счёта и (jumps, d2_exact) панели и ставки того же окна.
#
#   боевой (после F8, по команде CEO):  cd ~/xdl9/sel/waves && bash ~/xdl9/tools/statarb/xdl9/f8_aggregate.sh
#   сухой (формирование):               bash f8_aggregate.sh ~/xdl9/audit2/f8dry/run 2468 2479 ~/xdl9/form ~/xdl9/funding_form "СУХОЙ ПРОГОН НА ФОРМИРОВАНИИ (сутки 2468-2478)"
#
# Аргументы: [каталог прогона F8_CWD] [d0] [d1] [каталог панелей] [каталог ставок] [метка отчёта]. Входы-справочники лежат в ~/xdl9/sel (копии из репозитория, sha256 в params f8_aggregator_inputs)
# и в ~/xdl9/tools/statarb/xdl9/slices (определения срезов). Выход - $RUN/agg (существующий каталог agg стирается: он производный).
set -eu
RUN="${1:-$PWD}"
D0="${2:-2479}"
D1="${3:-2500}"
PAN="${4:-$HOME/xdl9/trade}"
FUND="${5:-$HOME/xdl9/funding}"
LABEL="${6:-}"
PY="${PY:-$HOME/xdl9/venv/bin/python}"
X="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEL="${F8_SEL:-$HOME/xdl9/sel}"
DEFS="${F8_SLICE_DEFS:-$HOME/xdl9/sel/f8_slices}"
INST="${F8_INST:-$HOME/xdl9/inst}"
CELLS_PARAMS="$SEL/P-37-params-cells-2026-11-06.csv"
PARAMS="$SEL/P-37-params-2026-11-06.csv"
for f in "$SEL/contracts.csv" "$SEL/d2_pairs.csv" "$CELLS_PARAMS" "$PARAMS" "$DEFS/slices_days.csv"; do [ -e "$f" ] || { echo "ОТКАЗ: нет входа агрегации $f"; exit 2; }; done
cd "$RUN"
[ -d out ] || { echo "ОТКАЗ: нет каталога out в $RUN"; exit 2; }
rm -rf agg
rc=0
"$PY" "$X/f8_agg.py" cells --run out --out agg --contracts "$SEL/contracts.csv" --cells-params "$CELLS_PARAMS" --d2-pairs "$SEL/d2_pairs.csv" --d0 "$D0" --d1 "$D1" || rc=$?
"$PY" "$X/f8_agg.py" slices --run out --defs "$DEFS" --out agg --contracts "$SEL/contracts.csv" --inst-dir "$INST" --params "$PARAMS" --flags out/flags/pair_flags.csv --d2-pairs "$SEL/d2_pairs.csv" --d0 "$D0" --d1 "$D1"
"$PY" "$X/f8_agg.py" controls --run out --out agg --d0 "$D0" --d1 "$D1"
"$PY" "$X/f8_agg.py" jumps --run out --contracts "$SEL/contracts.csv" --panels "$PAN" --out agg --d0 "$D0" --d1 "$D1"
"$PY" "$X/d2_exact_funding.py" run --run out --panels "$PAN" --funding "$FUND" --out agg/d2exact --stages w1
"$PY" "$X/f8_report.py" --agg agg --sel "$SEL/sel_doc" --cells-params "$CELLS_PARAMS" --out agg/f10_draft.md --table1 agg/table1.csv --label "$LABEL"
echo "агрегация готова: $RUN/agg (код cells: $rc; 3 = ошибка сверок consistency.csv)"
exit "$rc"
