#!/bin/bash
# TK-048: alpha-b22 (VPS) -> сервер счёта + обёртки f0/f1 (ALPHA_DIRECT_FEED=0|1)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
B=/opt/alpha-compute/bin
ssh "${K[@]}" root@13.140.29.171 "cat $B/alpha-b22" | ssh "${K[@]}" root@89.163.242.211 "cat > $B/alpha-b22 && chmod +x $B/alpha-b22 && md5sum $B/alpha-b22"
for f in 0 1; do
  ssh "${K[@]}" root@89.163.242.211 "printf '#!/bin/bash\nexport ALPHA_DIRECT_FEED=$f\nexec $B/alpha-b22 \"\$@\"\n' > $B/alpha-b22-f$f && chmod +x $B/alpha-b22-f$f"
done
ssh "${K[@]}" root@89.163.242.211 "cat $B/alpha-b22-f1"
