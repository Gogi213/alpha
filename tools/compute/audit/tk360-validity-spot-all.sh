#!/usr/bin/env bash
# TK-360 проход валидности СПОТА 1–14.02: планировщик по памяти (tk360-memq.py, задача = сутки × символ × F1 F2 F3), затем повтор трёх
# символов суток 01 (детерминизм: md5 rounds/forms) и проверка инвариантов (tk360-validity-spot-check.py).
#   tk360-validity-spot-all.sh <выход-каталог>        env: NX (15) BUDGET (80)
set -uo pipefail
O=$1; D=/data/tk0360; export NX=${NX:-15} BUDGET=${BUDGET:-80}
mkdir -p "$O"
python3 $D/tk360-memq.py "$O" 2026-02-01,2026-02-02,2026-02-03,2026-02-04,2026-02-05,2026-02-06,2026-02-07,2026-02-08,2026-02-09,2026-02-10,2026-02-11,2026-02-12,2026-02-13,2026-02-14
rc=$?
echo "memq rc=$rc $(date +%s)" >> "$O/units.txt"
syms=$(ls -d "$O"/2026-02-01-*-F3 2>/dev/null | sed 's#.*/2026-02-01-\(.*\)-F3#\1#' | sort | sed -n '1p;20p;40p')
bad=0; n=0
for s in $syms; do
  TAG=$s GT=1 bash $D/tk360-validity-spot.sh 2026-02-01 "$O-B" "$s"
  for u in "$O"/2026-02-01-$s-F*; do
    b="$O-B/$(basename "$u")"
    for f in "$u"/out/*/rounds.csv "$u"/out/*/forms.csv; do
      r=${f#"$u"/}; n=$((n+1))
      [ "$(md5sum < "$f")" = "$(md5sum < "$b/$r" 2>/dev/null)" ] || { bad=$((bad+1)); echo "РАЗЛИЧИЕ $u/$r" >> "$O/determinism.txt"; }
    done
  done
done
echo "детерминизм ($syms): файлов $n, различий $bad" | tee -a "$O/determinism.txt"
NP=${NX} python3 $D/tk360-validity-spot-check.py "$O" "$O/check"
echo "check rc=$?" >> "$O/units.txt"
touch "$O/done"
exit $rc
