#!/usr/bin/env bash
# TK-347: сборка головы → MYX 02-13/02-01 новым бинарём (раньше «Order id exists») → гейт «байт в байт» старый/новый на 02-05.
#   под alsched: python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk347-gate --max-runtime 2h --cores 3 --mem 16 -- bash /data/tk0347/tk347-gate.sh
# Требует: /data/tk0347 с src.tgz/files.lst/HEAD (push-head.sh) и /data/tk0345/tk345-validity-pass.sh (проход валидности TK-345).
set -uo pipefail
W=/data/tk0347; OLD=${OLD:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; PASS=/data/tk0345/tk345-validity-pass.sh
JOBS=${JOBS:-3} bash "$(dirname "$0")/calc-build.sh" "$W" 2>&1 | tail -3 || exit 1
NEW=$W/alpha-new; [ -x "$NEW" ] || { echo "нет бинаря"; exit 2; }
export GT=3 SH=6 GRP=3
for u in "2026-02-13 F1 F2 F3" "2026-02-01 F2"; do
  set -- $u; d=$1; shift
  BIN=$NEW PROBE_SYMS=MYXUSDT FAMS="$*" TAG=myx bash "$PASS" "$d" 0 "$W/myx"; echo "myx $d rc=$?"
done
for v in old new; do
  [ $v = old ] && B=$OLD || B=$NEW
  BIN=$B PROBE_SYMS="0GUSDT 1000BONKUSDT 1000PEPEUSDT" TAG=g bash "$PASS" 2026-02-05 0 "$W/gate-$v"; echo "gate $v rc=$?"
done
for v in old new; do ( cd "$W/gate-$v" && find . -path '*/out/*' -type f | LC_ALL=C sort | xargs md5sum ) > "$W/gate-$v.md5"; done
wc -l "$W/gate-old.md5" "$W/gate-new.md5"
if diff -q "$W/gate-old.md5" "$W/gate-new.md5" >/dev/null && [ -s "$W/gate-old.md5" ]; then echo "GATE IDENTICAL"; else echo "GATE DIFF"; diff "$W/gate-old.md5" "$W/gate-new.md5" | head; exit 3; fi
