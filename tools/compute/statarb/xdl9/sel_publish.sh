#!/usr/bin/env bash
# TK-493 S1/S2: малые таблицы формирования из data/xdl9/sel/ (gitignored) в docs/findings/xdl9-selection-2026-11-06/ (в git); r2 (F7) - docs/archive/xdl9-selection-r2-2026-11-06/,
# S1 (r3.1) - docs/archive/xdl9-selection-s1-2026-11-06/. Запуск на Deck из зеркала дерева (~/xdl9/wt) или на ПК после переноса sel/.
set -eu
cd "$(dirname "$0")/../../../.."
S=data/xdl9/sel
D=docs/findings/xdl9-selection-2026-11-06
mkdir -p "$D"
for f in catalogs.csv session_assets.csv contracts.csv splits.csv a1_pairs.csv b1_baskets.csv catalog_meta.json funding_interval_form.csv d2_pairs.csv d2_params.csv d2_excluded.csv d2_durations.csv; do cp "$S/$f" "$D/$f"; done
for f in us_audit/us_rule_audit40.csv us_audit/us_rule_nonus_flagged.csv us_audit/us_rule_label_misses.csv; do [ -f "$S/$f" ] && cp "$S/$f" "$D/$(basename "$f")"; done
rm -rf "$D/waves"; mkdir -p "$D/waves"
cp -r "$S/waves/." "$D/waves/"
cp "$S/smoke_cmds.sh" "$D/smoke_cmds.sh" 2>/dev/null || true
cp "$S/waves/smoke_out/smoke.log" "$D/smoke.log" 2>/dev/null || true
rm -rf "$D/waves/smoke_out" "$D/waves/smoke_d2" "$D/waves/out" "$D/waves/d2"      # дымовая проба на формировании - не часть списков волн; выходов счёта в дереве нет
find "$D/waves" -name '*.sh' -exec sed -i 's/\r$//' {} +
