#!/bin/bash
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
D="$(cd "$(dirname "$0")" && pwd)"
ssh "${K[@]}" root@89.163.242.211 "cat > /opt/alpha-compute/bin/alpha-b23pgoflag && chmod +x /opt/alpha-compute/bin/alpha-b23pgoflag && md5sum /opt/alpha-compute/bin/alpha-b23pgoflag" < "$D/alpha-b23pgoflag.sh"
