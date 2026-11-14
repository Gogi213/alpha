#!/usr/bin/env bash
# Один этап П-42 на srv: variants-<stage>.txt → прогон под systemd-run (7 потоков, Nice=19, CPUQuota=600% -p CPUWeight=1, idle IO) → data/p42/<stage>/
# Использование: bash tools/p42/runstage.sh <stage> [бинарь на srv]
set -euo pipefail
STAGE="${1:?stage}"; BIN="${2:-/data/p42/bin/alpha-p42-v3}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
scp -q "${K[@]}" "data/p42/variants-$STAGE.txt" "$H:/data/p42/"
UNIT="p42-$STAGE-$(date +%s)"
ssh "${K[@]}" "$H" "rm -rf /data/p42/out/$STAGE; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle --collect $BIN breakout run --cache /data/p42/cache --variants /data/p42/variants-$STAGE.txt --out /data/p42/out/$STAGE --threads 6 >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 1; done; journalctl -u $UNIT --no-pager | tail -2 | cut -c1-200; tail -1 /data/p42/lag.log"
rm -rf "data/p42/$STAGE"
ssh "${K[@]}" "$H" "tar -C /data/p42/out -czf - $STAGE" | tar -m -C data/p42 -xzf -
echo "pulled: $(ls data/p42/$STAGE | wc -l) entries"
