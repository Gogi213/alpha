#!/bin/bash
# TK-068/TK-081: производство без очереди (голая systemd-run, jobrun.sh) — реестр спрашивается до старта, итог пишется после.
# regrun.sh <класс> <команда…>; пересчёт — GUARD_RECOMPUTE=1 GUARD_WHY="причина", замер против шума — GUARD_REPEAT=N.
# Пример: systemd-run --unit tk064-x --collect -p CPUQuota=400% /data/registry/regrun.sh prod bash /data/tk064/run.sh
cls=$1; shift
G=/data/registry/guard.py
PEND=""
if [ -z "$GUARD_PENDING" ]; then          # внутри alsched submit проверка уже была (хвост пишет done сам)
  gout=$(python3 $G check "$cls" ${GUARD_RECOMPUTE:+--recompute --why "$GUARD_WHY"} ${GUARD_REPEAT:+--repeat "$GUARD_REPEAT"} -- "$@"); grc=$?
  printf '%s\n' "$gout" | grep -v '^GUARD-'
  case $grc in
    0) PEND=$(printf '%s\n' "$gout" | sed -n 's/^GUARD-PENDING //p')
       eval "set -- $(printf '%s\n' "$gout" | sed -n 's/^GUARD-ARGV //p')" ;;
    3) exit 0 ;;                          # всё уже посчитано: «взято из реестра», считать нечего
    2) exit 2 ;;                          # --recompute без причины
    *) echo "guard: проверка сломалась (rc=$grc) — запуск без реестра" >&2 ;;
  esac
fi
REGM=$(python3 /data/registry/snap.py begin "$cls" "$@" 2>/dev/null) || REGM=""
"$@"; rc=$?
[ -n "$REGM" ] && GUARD_PENDING=${PEND:-$GUARD_PENDING} python3 /data/registry/snap.py end "$REGM" "$rc" 2>/dev/null
if [ -n "$PEND" ]; then
  python3 $G done "$cls" "$rc" --pending "$PEND" || echo "guard: done не записан (см. done-errors.log)" >&2
fi
exit $rc
