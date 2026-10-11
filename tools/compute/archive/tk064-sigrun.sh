#!/bin/bash
# G3 signals повтор: сутки 05.01 (в пуле там 38 сигналов B1), монеты с сигналами; запуск: bash tk064-sigrun.sh (юнит tk064-sig на сервере счёта, итог /data/tk064/sig19b.out)
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
ssh "${K[@]}" root@89.163.242.211 "systemctl reset-failed tk064-sig 2>/dev/null; systemd-run --quiet --unit tk064-sig --collect -p CPUQuota=800% env SIG_DAY=2026-01-05 SYMS='ATOMUSDT ENAUSDT ARBUSDT BNBUSDT ADAUSDT LTCUSDT' bash -c '/data/benchrun.sh stand bash /data/tk064/tk064-sig.sh b19head b19r1 /data/tk064/sig19b > /data/tk064/sig19b.out 2>&1; echo done >> /data/tk064/sig19b.out' && echo юнит tk064-sig запущен"
