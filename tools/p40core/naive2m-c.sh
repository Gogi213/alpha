#!/bin/bash
# TK-522: добор крупных активов b-списка (пик shm > брони b) одним юнитом с SHM 0,8 после конца b (K1 −60 с)
while systemctl is-active -q p40-d1naive2mb; do sleep 20; done
OUT=/root/p40core/out_d1naive2m
A=$(python3 - <<'EOF'
import os
out = "/root/p40core/out_d1naive2m"
b = [a for a in open("/root/p40core/naive2_b.txt").read().split(",") if a]
print(",".join(a for a in b if not os.path.exists(f"{out}/{a}/2026-09-17+14/DONE")))
EOF
)
echo "добор: $A"
[ -n "$A" ] && bash /root/p40core/naive-run522m.sh $OUT "$A"
date
