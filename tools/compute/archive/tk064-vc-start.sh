#!/usr/bin/env bash
# tk064-vc-start.sh <дерево>: vps-проверка (fmt+clippy+test) юнитом tk064-vc на VPS; маркер /opt/alpha-compute/tk064vc.done, вывод tk064vc.out
set -euo pipefail
TREE="${1:?дерево}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@13.140.29.171
ARC="/c/visual projects/alpha/data/tk064vc.tgz"
(cd "$TREE" && git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC")
scp -q "${KEY[@]}" "$ARC" "$HOST:/opt/alpha-compute/tk064vc.tgz"
scp -q "${KEY[@]}" "$(dirname "$0")/tk064-vc.sh" "$HOST:/opt/alpha-compute/tk064-vc.sh"
ssh "${KEY[@]}" "$HOST" "systemctl reset-failed tk064-vc 2>/dev/null; systemd-run --quiet --unit=tk064-vc bash /opt/alpha-compute/tk064-vc.sh && echo vc-started"
