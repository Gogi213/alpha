#!/usr/bin/env bash
# П-42 §13.16: запуск `breakout tick` (566 монет, тики) на srv под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) и ВОЗВРАТ СРАЗУ
# (ожидание — отдельно: bash tools/srv-wait.sh unit:<имя юнита> <минут>, фоном; п. 33 приказов). Результат на srv: /data/p42-t566/out/<NAME>/<вариант>/,
# журнал /tmp/p42-t566-<NAME>.log, по окончании файл out/<NAME>/DONE (код возврата — в нём).
# Использование: [P42_DETAIL=1] [P42_SIGS=<от>:<до>] [P42_FTAB=<csv на srv>] bash tools/p42/runentry.sh <NAME> <локальный файл вариантов> <бинарь на srv>
set -euo pipefail
NAME="${1:?имя прогона}"; VF="${2:?файл вариантов}"; BIN="${3:?бинарь на srv}"
SRV=/data/p42-t566; TH=6
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
ENVS=""
[ -n "${P42_DETAIL:-}" ] && ENVS="$ENVS P42_DETAIL=1"
[ -n "${P42_SIGS:-}" ] && ENVS="$ENVS P42_SIGS=$P42_SIGS"
FT=""; [ -n "${P42_FTAB:-}" ] && FT=" --ftab $P42_FTAB"  # таблица кандидатов признаков F (v21), путь на srv
ssh "${K[@]}" "$H" "cat > $SRV/variants-$NAME.txt" < "$VF"
ssh "${K[@]}" "$H" "cat > $SRV/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/$NAME
env$ENVS $BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt --symbols "\$(cat $SRV/symbols.txt)" --out $SRV/out/$NAME --threads $TH$FT > /tmp/p42-t566-$NAME.log 2>&1
rc=\$?
mkdir -p $SRV/out/$NAME
echo \$rc > $SRV/out/$NAME/DONE
[ \$rc -eq 0 ] || echo "FAIL $NAME rc=\$rc" >> /tmp/p42-t566-$NAME.log
EOS
UNIT="p42-t566-$NAME"
ssh "${K[@]}" "$H" "systemctl reset-failed $UNIT 2>/dev/null; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/run-$NAME.sh >/dev/null 2>&1 && echo started $UNIT"
