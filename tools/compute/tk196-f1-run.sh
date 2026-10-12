#!/usr/bin/env bash
# TK-196 П-14 F1: режим месяцев (+6/8/12/24 ч) и затем все B1-сутки месяцев одним проходом по суткам, D суток вразброс; метка /data/tk0196/f1.done.
#   tk196-f1-run.sh [мес...]   env: D=3 GT=2 (PROBE=<сутки> — только эти сутки, метка probe.done)
set -uo pipefail
D=${D:-3}; export GT=${GT:-2}; T=/data/tk0196
mons=("$@"); [ ${#mons[@]} -gt 0 ] || mons=(jan feb mar apr may jun jul aug sep)
for m in "${mons[@]}"; do [ -e $T/regime/$m/.ok ] || { python3 $T/regime-long.py /data/tk046/$m/home/alpha/epochs/e-$m/study/regime $T/regime/$m && touch $T/regime/$m/.ok; } || exit 4; done
if [ -n "${PROBE:-}" ]; then m=$(awk -F, -v d="$PROBE" '$2==d{print $1; exit}' /data/tk0115/delta/b1-symdays.csv); bash $T/day.sh "$PROBE" "$m" "$PROBE"; touch $T/probe.done; exit 0; fi
for m in "${mons[@]}"; do awk -F, -v m="$m" 'NR>1 && $1==m{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u; done \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0196/day.sh "$1" "$0" "$1"' 2>&1
touch $T/f1.done
