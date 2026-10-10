#!/bin/bash
# TK-048: обёртка alpha-b23-pgo-f1 (ALPHA_DIRECT_FEED=1) на сервере счёта
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
B=/opt/alpha-compute/bin
ssh "${K[@]}" root@89.163.242.211 "printf '#!/bin/bash\nexport ALPHA_DIRECT_FEED=1\nexec $B/alpha-b23-pgo \"\$@\"\n' > $B/alpha-b23-pgo-f1 && chmod +x $B/alpha-b23-pgo-f1 && cat $B/alpha-b23-pgo-f1"
