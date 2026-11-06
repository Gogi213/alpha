#!/usr/bin/env bash
# TK-493 S1: малые таблицы формирования из data/xdl9/sel/ (gitignored) в docs/findings/xdl9-selection-2026-11-06/ (в git); r2 (F7) лежит в docs/archive/xdl9-selection-r2-2026-11-06/
set -eu
cd "$(dirname "$0")/../../../.."
S=data/xdl9/sel
D=docs/findings/xdl9-selection-2026-11-06
mkdir -p "$D"
for f in catalogs.csv session_assets.csv contracts.csv splits.csv a1_pairs.csv b1_baskets.csv catalog_meta.json; do cp "$S/$f" "$D/$f"; done
rm -rf "$D/waves"; mkdir -p "$D/waves"
cp -r "$S/waves/." "$D/waves/"
find "$D/waves" -name '*.sh' -exec sed -i 's/\r$//' {} +
