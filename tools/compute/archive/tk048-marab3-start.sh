#!/bin/bash
# TK-048: доставить march-ab3.sh на сервер счёта и запустить юнитом через benchrun2 (как tk048-marab)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
D="$(cd "$(dirname "$0")" && pwd)"
cat "$D/tk048-march-ab3.sh" | ssh "${K[@]}" root@89.163.242.211 "cat > /data/tk048/march-ab3.sh && chmod +x /data/tk048/march-ab3.sh && systemd-run --unit tk048-marab3 --collect /data/tk052/benchrun2.sh stand bash /data/tk048/march-ab3.sh"
