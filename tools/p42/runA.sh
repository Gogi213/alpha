#!/usr/bin/env bash
# Шаг A П-42 v2 (§13.1): после stage1 — ext → joint → final → report. Env: P42_ROOT=data/p42v2.
set -euo pipefail
export P42_SRV=/data/p42v2 P42_LOCAL=data/p42v2 P42_ROOT=data/p42v2 P42_FQ_COL=first_day_ge_12M
BIN=/data/p42v2/bin/alpha-p42-v5
cd "$(dirname "$0")/../.."
for st in ext joint final; do
  python tools/p42/phase.py $st
  bash tools/p42/runstage.sh $st $BIN
done
