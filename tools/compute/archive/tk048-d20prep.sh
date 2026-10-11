#!/bin/bash
# tk048-d20prep.sh <мес...>: подготовка (без счёта) мар–окт для полного пула — D20 новых монет, свечи/σ, дом, regime. По месяцу под эксклюзивным замком stand/волн.
# Запуск: systemd-run --unit tk048-d20prep --collect -p CPUQuota=1500% bash /opt/alpha-compute/bin/tk048-d20prep.sh mar apr may jun jul aug sep oct   (маркер /data/tk048/d20prep.done)
B=/opt/alpha-compute/bin; mkdir -p /data/progress
for m in "$@"; do
  [ -e /data/tk046/$m/.prep_done ] && continue
  flock /data/tk-bench.lock env PREP_ONLY=1 bash $B/tk046-month.sh $m > /data/tk046/$m.prep.log 2>&1 || { echo "FAIL $m rc=$?" >> /data/tk048/d20prep.log; exit 1; }
  echo "ok $m $(date -Is)" >> /data/tk048/d20prep.log
done
touch /data/tk048/d20prep.done
