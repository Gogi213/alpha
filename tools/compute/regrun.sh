#!/bin/bash
# TK-068: производство без замка (голой systemd-run) тоже пишет строку реестра: regrun.sh <класс> <команда…>
# Пример: systemd-run --unit tk064-x --collect -p CPUQuota=400% /data/registry/regrun.sh prod bash /data/tk064/run.sh
cls=$1; shift
REGM=$(python3 /data/registry/snap.py begin "$cls" "$@" 2>/dev/null) || REGM=""
"$@"; rc=$?
[ -n "$REGM" ] && python3 /data/registry/snap.py end "$REGM" "$rc" 2>/dev/null
exit $rc
