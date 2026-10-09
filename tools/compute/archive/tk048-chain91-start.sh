#!/bin/bash
# chain91 стартует после конца чужого tk048-chain90 (TK-064): общий /dev/shm/abin-t46m
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
D="$(cd "$(dirname "$0")" && pwd)"
ssh "${K[@]}" root@89.163.242.211 "test ! -e /data/tk048/chain91.sh && cat > /data/tk048/chain91.sh && chmod +x /data/tk048/chain91.sh && systemd-run --unit tk048-chain91-q --collect bash -c 'while systemctl is-active -q tk048-chain90; do sleep 10; done; exec bash /data/tk048/chain91.sh'" < "$D/tk048-chain91.sh"
