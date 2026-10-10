#!/bin/bash
# TK-048 п.0а: бинарник пробы VPS -> сервер счёта, скрипт замера -> /data/tk048, запуск юнитом tk048-ecprobe
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
D="$(cd "$(dirname "$0")" && pwd)"
ssh "${K[@]}" root@13.140.29.171 "cat /opt/alpha-compute/bin/alpha-b24probe" | ssh "${K[@]}" root@89.163.242.211 "cat > /opt/alpha-compute/bin/alpha-b24probe && chmod +x /opt/alpha-compute/bin/alpha-b24probe && md5sum /opt/alpha-compute/bin/alpha-b24probe"
ssh "${K[@]}" root@89.163.242.211 "cat > /data/tk048/ecprobe.sh && chmod +x /data/tk048/ecprobe.sh && systemd-run --quiet --unit=tk048-ecprobe -p CPUQuota=100% bash /data/tk048/ecprobe.sh && echo запущено" < "$D/tk048-ecprobe.sh"
