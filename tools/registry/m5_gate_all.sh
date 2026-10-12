#!/bin/bash
# Гейт М5 «выкл = прежний» по месяцам: m5_gate_all.sh <рабочий каталог> <мес>... (calc, юнитом alsched)
W=${1:?раб}; shift; rc=0
for m in "$@"; do
  python3 "$(dirname "$0")/closure_mech.py" --gate "$W/gate-2026-$m" --month "2026-$m" || rc=1
done
exit $rc
