#!/usr/bin/env bash
# TK-064 п.5: size_at_arm по всем месяцам пула (tk064-lvl.sh), сутки — из signals/ и signals-retry/ метки месяца.
set -uo pipefail
for m in jan feb mar apr may jun jul aug sep oct; do
  O=/data/tk064/pool/m-$m
  days=$( { ls "$O/signals" 2>/dev/null; ls "$O/signals-retry" 2>/dev/null; } | sort -u)
  # shellcheck disable=SC2086
  D=${D:-3} TP=${TP:-4} bash /data/tk064/tk064-lvl.sh m-$m $m $days
done
touch /data/tk064/lvl-all2.done
