#!/bin/bash
# G1/G3 финально на b17r1e: gate.sh b17head vs b17r1e (off побайтно, on — не-approaches побайтно), SUI и AAVE 20.09 -> /data/tk064/gate3.out
REPS=1 /data/benchrun.sh stand /data/tk064/gate.sh /opt/alpha-compute/bin/b17head /opt/alpha-compute/bin/b17r1e /data/tk064/g3 e-sep:SUIUSDT:2026-09-20 e-sep:AAVEUSDT:2026-09-20 > /data/tk064/gate3.out 2>&1
echo done >> /data/tk064/gate3.out
