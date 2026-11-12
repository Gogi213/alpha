#!/usr/bin/env bash
# Состояние очереди счёта (`alpha-gridq`): queue/STATUS (демон переписывает каждый такт) и хвост журнала.
#   q-status.sh [--failed] [--tag <серия>]
#   --failed  упавшие задания: rc, пик, OOM, заметка, последняя строка лога
#   --tag     готовые/упавшие одной серии и их средняя длительность (для оценки конца)
set -uo pipefail
Q="${GRIDQ_DIR:-$HOME/alpha/queue}"
[ -f "$Q/STATUS" ] || { echo "нет $Q/STATUS — демон alpha-gridq не запускался"; exit 1; }
cat "$Q/STATUS"
systemctl --user is-active -q alpha-gridq 2>/dev/null || echo "!! демон alpha-gridq не активен"
echo "-- журнал:"; tail -5 "$Q/gridq.log" 2>/dev/null
kv() { awk -F= -v k="$1" '$1 == k {v = substr($0, length(k) + 2)} END {print v}' "$2"; }
case "${1:-}" in
  --failed)
    for j in "$Q"/failed/*.job; do
      [ -e "$j" ] || continue
      echo "$(basename "$j" .job): rc $(kv JOB_RC "$j"), пик $(kv JOB_PEAK_MB "$j") МБ, oom $(kv JOB_OOM "$j") $(kv JOB_NOTE "$j")"
      echo "   лог: $(tail -1 "$(kv JOB_LOG "$j")" 2>/dev/null | cut -c1-200)"
    done;;
  --tag)
    t="$2"; n=0; s=0
    for j in "$Q"/done/*-"$t"-*.job; do
      [ -e "$j" ] || continue
      a=$(date -u -d "$(kv JOB_START "$j")" +%s); b=$(date -u -d "$(kv JOB_END "$j")" +%s); n=$((n + 1)); s=$((s + b - a))
    done
    p=$(ls "$Q/pending" | grep -c -- "-$t-"); r=$(ls "$Q/running" | grep -c -- "-$t-"); f=$(ls "$Q/failed" | grep -c -- "-$t-")
    echo "серия $t: готово $n, идёт $r, ждёт $p, упало $f; среднее задание $([ $n -gt 0 ] && echo $((s / n)) || echo —) с";;
esac
