#!/bin/bash
# TK-072: запуск задания на calc с уникальным именем юнита <метка>-<runid> и записью InvocationID.
# jobrun.sh <метка, напр. tk064-r1> [-p СВОЙСТВО…] -- <класс regrun: prod|…> <команда…>
# Пишет /data/progress/runs/<unit>.json {unit, runid, invocation, started}; печатает «UNIT=… INV=…».
# wait_for: host:calc:unit:<unit> — конец придёт событием шины (watcher: «юнит.запущен/остановлен» с тем же InvocationID), ssh не нужен.
label=$1; shift
props=()
while [ "$1" != "--" ] && [ $# -gt 0 ]; do props+=("$1"); shift; done
[ "$1" = "--" ] && shift
[ -n "$label" ] && [ $# -ge 2 ] || { echo "jobrun.sh <метка> [-p …] -- <класс> <команда…>" >&2; exit 2; }
runid=$(date +%m%d%H%M%S)-$(head -c2 /dev/urandom | od -An -tx1 | tr -d ' \n')
unit="$label-$runid"
systemd-run --unit "$unit" --collect "${props[@]}" /data/registry/regrun.sh "$@" >/dev/null 2>&1 || { echo "systemd-run: отказ" >&2; exit 1; }
inv=""
for _ in 1 2 3 4 5 6 7 8 9 10; do inv=$(systemctl show -p InvocationID --value "$unit.service" 2>/dev/null); [ -n "$inv" ] && break; sleep 0.3; done
mkdir -p /data/progress/runs
printf '{"unit":"%s","runid":"%s","invocation":"%s","started":"%s"}\n' "$unit" "$runid" "$inv" "$(date -Is)" > "/data/progress/runs/$unit.json"
echo "UNIT=$unit INV=$inv"
