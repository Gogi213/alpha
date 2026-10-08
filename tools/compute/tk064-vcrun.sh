#!/usr/bin/env bash
# tk064-vcrun.sh <дерево>: vps-check all юнитом tk064-vc на VPS (ожидатель замка переживает сессию); маркеры /opt/alpha-compute/tk064vc.done, итог tk064vc.out
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts); H=root@13.140.29.171
D=$(cd "$(dirname "$0")" && pwd); T="$1"
(cd "$T" && git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf /tmp/tk064vc.tgz)
scp -q "${K[@]}" /tmp/tk064vc.tgz $H:/opt/alpha-compute/tk064vc.tgz
ssh "${K[@]}" $H 'cat > /opt/alpha-compute/tk064vc.sh && systemctl reset-failed tk064-vc 2>/dev/null; systemd-run --quiet --unit=tk064-vc bash /opt/alpha-compute/tk064vc.sh && echo юнит tk064-vc запущен' < "$D/tk064-vc.sh"
