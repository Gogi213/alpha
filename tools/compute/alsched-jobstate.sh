#!/bin/sh
# Адаптер сторожа жизни плагина (RPV_JOB_STATE_CMD, форма wait_for `job:calc:<id>`, TK-092): состояние задания alsched.
# 1-я строка — running|queued|done|failed|missing; при failed далее rc и хвост лога. Только чтение, нагрузки нет.
d=${SCHED_DIR:-/data/sched}; id=$1; j=$d/jobs/$id.json
[ -f "$j" ] || { echo missing; exit 0; }
st=$(grep -o '"state": "[a-z]*"' "$j" | head -1 | cut -d'"' -f4)
rc=$(grep -o '"rc": -\{0,1\}[0-9]*' "$j" | head -1 | grep -o -- '-\{0,1\}[0-9]*$')
case "$st" in
  queued|running) echo "$st"; exit 0 ;;
  done) [ "${rc:-0}" = 0 ] && { echo done; exit 0; }; echo failed; echo "rc=$rc" ;;
  *) echo missing; exit 0 ;;
esac
tail -n 30 "$d/logs/$id.log" 2>/dev/null
