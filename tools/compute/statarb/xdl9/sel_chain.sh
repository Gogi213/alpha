#!/usr/bin/env bash
# TK-493 S2 (П-37 r3.2): ПОЛНАЯ цепочка пересчёта отбора на Steam Deck от каталогов до таблиц, списков волн и params. Только ФОРМИРОВАНИЕ 01.10-14.10; торговое окно не читается.
# Запуск (после dsync кода и положенных на Deck входов, см. отчёт S2 §1): systemd-run --user --unit=s2-chain --working-directory=$HOME/xdl9 env THREADS=8 bash <этот файл>
# Зеркало дерева на Deck: ~/xdl9/wt (tools/, docs/, data/xdl9 -> ~/xdl9). Перед запуском положить в ~/xdl9/wt/docs/research/ params ИЗ GIT (git show HEAD:...), без правок.
# Порядок (каждый шаг пишет в журнал ~/xdl9/sel/chain.log): funding_intervals -> sel_catalog -> панели (mask, form, formsh, links) -> form (real + 6 запусков выравниваний)
# -> sel_fdr -> sel_reports -> sel_g9 -> sel_b1 -> sel_cells -> sel_entry (gen, запуски, parse) -> sel_d2 -> sel_waves -> sel_sigma (gen, запуски, parse) -> sel_waves
# -> sel_smoke -> asset_us_rule_audit -> sel_publish -> sel_params_write. Время на Deck (8 потоков) ≈ 14 минут, из них form ≈ 8.
set -u
export THREADS=${THREADS:-8}
HOME_X=$HOME/xdl9
W=$HOME_X/wt/tools/compute/statarb/xdl9
PY=$HOME_X/venv/bin/python
SEL=$HOME_X/sel
DOCS=$HOME_X/wt/docs/findings/xdl9-selection-2026-11-06
LOG=$SEL/chain.log
mkdir -p "$SEL"
: > "$LOG"
step() { echo "$(date +%H:%M:%S) $*" >> "$LOG"; }
fail() { step "FAIL $*"; exit 1; }
cd "$W" || exit 1
step "очистка выходов S2 (входы: asset, inst, funding, funding_form, panels, panels_us, asset_map.csv, asset_pairs.csv не трогаются)"
for d in form_out cellpairs entry entry_out d2form d2form_out waves sigma_out us_audit; do rm -rf "${SEL:?}/$d"; done
rm -f "$SEL"/smoke_cmds.sh "$SEL"/sigma_cmds.* "$SEL"/a1_pairs.csv "$SEL"/b1_baskets.csv "$SEL"/d2_*.csv
rm -rf "$HOME_X/form_sh" "$HOME_X/cat"
rm -rf "$DOCS"; mkdir -p "$DOCS"
[ -d "$HOME_X/funding_form" ] || { step "fundcut"; python3 sel_deck_prep.py fundcut >> "$LOG" 2>&1 || fail fundcut; }
step "funding_intervals, sel_catalog"
python3 funding_intervals.py >> "$LOG" 2>&1 || fail funding_intervals
$PY sel_catalog.py >> "$LOG" 2>&1 || fail catalog
step "панели: mask, form, formsh, links"
for c in mask form formsh links; do python3 sel_deck_prep.py $c >> "$LOG" 2>&1 || fail "prep $c"; done
step "form (real + al_*)"
bash sel_form_all.sh >> "$LOG" 2>&1
grep -c FAIL "$SEL/form_out/form_all.log" >> "$LOG"
step "sel_fdr, sel_reports, sel_g9, sel_b1"
$PY sel_fdr.py >> "$LOG" 2>&1 || fail fdr
$PY sel_reports.py >> "$LOG" 2>&1 || fail reports
ln -sfn "$HOME_X/form" "$SEL/g9_sap"
$PY sel_g9.py gen >> "$LOG" 2>&1 && $PY sel_g9.py >> "$LOG" 2>&1 || fail g9
$PY sel_b1.py >> "$LOG" 2>&1 || fail b1
step "sel_cells"
mkdir -p "$SEL/cellpairs"
$PY sel_cells.py >> "$LOG" 2>&1 || fail cells
step "entry: gen, запуски, parse"
$PY sel_entry.py gen >> "$LOG" 2>&1 || fail "entry gen"
(cd "$SEL" && bash entry/entry_cmds.sh) >> "$LOG" 2>&1
grep -c FAIL "$SEL/entry_out/run.log" >> "$LOG"
$PY sel_entry.py parse >> "$LOG" 2>&1 || fail "entry parse"
step "sel_d2"
$PY sel_d2.py all >> "$LOG" 2>&1 || fail d2
cp "$SEL"/d2_params.csv "$SEL"/d2_excluded.csv "$SEL"/d2_durations.csv "$DOCS"/
step "waves #1, sigma gen/запуски/parse"
$PY sel_waves.py >> "$LOG" 2>&1 || fail waves
$PY sel_sigma.py gen >> "$LOG" 2>&1 || fail "sigma gen"
(cd "$SEL" && bash sigma_cmds.sh) >> "$LOG" 2>&1
grep -c FAIL "$SEL/sigma_out/run.log" >> "$LOG"
$PY sel_sigma.py parse >> "$LOG" 2>&1 || fail "sigma parse"
step "waves #2 (с sigma), smoke"
$PY sel_waves.py >> "$LOG" 2>&1 || fail waves2
$PY sel_smoke.py gen >> "$LOG" 2>&1 || fail "smoke gen"
(cd "$SEL/waves" && bash ../smoke_cmds.sh) >> "$LOG" 2>&1
grep -E "^(OK|FAIL)" "$SEL/waves/smoke_out/smoke.log" >> "$LOG"
[ -e "$HOME_X/out" ] || [ -e "$SEL/waves/out" ] && step "ОШИБКА: выходы счёта существуют"
step "правило США/не-США: точность"
python3 asset_us_rule_audit.py --out "$SEL/us_audit" >> "$LOG" 2>&1 || fail us_audit
step "publish, params"
bash sel_publish.sh >> "$LOG" 2>&1 || fail publish
$PY sel_params_write.py >> "$LOG" 2>&1 || fail params
step "DONE"
