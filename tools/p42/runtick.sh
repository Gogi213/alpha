#!/usr/bin/env bash
# Шаг B П-42 (§13.2) на srv: variants-<name>.txt → `breakout tick` под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) → <локально>/tick-<name>/
# Использование: [P42_SRV=/data/p42v2 P42_LOCAL=data/p42v2 P42_THREADS=6] bash tools/p42/runtick.sh <name> [бинарь на srv]
set -euo pipefail
NAME="${1:?name}"; SRV="${P42_SRV:-/data/p42v2}"; LOC="${P42_LOCAL:-data/p42v2}"; BIN="${2:-$SRV/bin/alpha-p42-v6}"; TH="${P42_THREADS:-6}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
scp -q "${K[@]}" "$LOC/variants-$NAME.txt" "$H:$SRV/"
UNIT="p42-tick-$NAME-$(date +%s)"
ssh "${K[@]}" "$H" "rm -rf $SRV/out/tick-$NAME; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect $BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt --out $SRV/out/tick-$NAME --threads $TH >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 1; done; journalctl -u $UNIT --no-pager | grep -E 'фаза|готово|ОШИБК|error' | cut -c1-260 | tail -6"
rm -rf "$LOC/tick-$NAME"
ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - tick-$NAME" | tar -m -C "$LOC" -xzf -
echo "pulled: $(ls $LOC/tick-$NAME | wc -l) entries"
