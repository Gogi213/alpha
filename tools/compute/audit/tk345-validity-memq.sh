#!/usr/bin/env bash
# TK-345: сутки 05–14.02 планировщиком по памяти (tk345-memq.py, задача = сутки × символ), затем повтор шарда 0 суток 01 (детерминизм).
#   tk345-validity-memq.sh <выход-каталог pass3> <каталог pass2 (сутки 01–04, SH=6)>
set -uo pipefail
O=$1; P2=$2
python3 /data/tk0345/tk345-memq.py "$O" 2026-02-05,2026-02-06,2026-02-07,2026-02-08,2026-02-09,2026-02-10,2026-02-11,2026-02-12,2026-02-13,2026-02-14
rc=$?
SH=6 bash /data/tk0345/tk345-validity-pass.sh 2026-02-01 0 "$P2-B"
bad=0; n=0
for u in "$P2"/2026-02-01-s0-g*-F*; do
  b="$P2-B/$(basename "$u")"
  for f in "$u"/out/*/rounds.csv "$u"/out/*/forms.csv; do
    r=${f#"$u"/}; n=$((n+1))
    [ "$(md5sum < "$f")" = "$(md5sum < "$b/$r" 2>/dev/null)" ] || { bad=$((bad+1)); echo "РАЗЛИЧИЕ $u/$r" >> "$O/determinism.txt"; }
  done
done
echo "детерминизм: файлов $n, различий $bad" | tee -a "$O/determinism.txt"
exit $rc
