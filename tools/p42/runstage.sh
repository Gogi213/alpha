#!/usr/bin/env bash
# Один этап П-42 на srv: variants-<stage>.txt → прогон под systemd-run (6 потоков, Nice=19, CPUQuota=600% -p CPUWeight=1, idle IO) → <локально>/<stage>/
# Использование: [P42_SRV=/data/p42v2 P42_LOCAL=data/p42v2] bash tools/p42/runstage.sh <stage> [бинарь на srv]
set -euo pipefail
STAGE="${1:?stage}"; SRV="${P42_SRV:-/data/p42}"; LOC="${P42_LOCAL:-data/p42}"; BIN="${2:-$SRV/bin/alpha-p42-v3}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
scp -q "${K[@]}" "$LOC/variants-$STAGE.txt" "$H:$SRV/"
UNIT="p42-$(basename "$SRV")-$STAGE-$(date +%s)"
ssh "${K[@]}" "$H" "rm -rf $SRV/out/$STAGE; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle --collect $BIN breakout run --cache $SRV/cache --variants $SRV/variants-$STAGE.txt --out $SRV/out/$STAGE --threads 6 >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 1; done; journalctl -u $UNIT --no-pager | tail -2 | cut -c1-200"
rm -rf "$LOC/$STAGE"
ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - $STAGE" | tar -m -C "$LOC" -xzf -
echo "pulled: $(ls $LOC/$STAGE | wc -l) entries"
