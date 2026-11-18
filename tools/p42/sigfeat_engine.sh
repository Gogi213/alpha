#!/usr/bin/env bash
# П-42 §13.17 (TK-588): прогон `breakout tick` v19 с дампами P42_SIGSX / P42_LEVELS / P42_SIGS / P42_DETAIL на srv под systemd-run
# (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) и ВОЗВРАТ СРАЗУ; ожидание — bash tools/srv-wait.sh file:/data/p42-t588/out/<NAME>/DONE.
# Использование: bash tools/p42/sigfeat_engine.sh <NAME> <файл вариантов> <бинарь на srv>
set -euo pipefail
NAME="${1:?имя прогона}"; VF="${2:?файл вариантов}"; BIN="${3:?бинарь на srv}"
W=/data/p42-t588; TH=6
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
ssh "${K[@]}" "$H" "mkdir -p $W/out && cat > $W/variants-$NAME.txt" < "$VF"
ssh "${K[@]}" "$H" "cat > $W/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $W
rm -rf out/$NAME
env P42_DETAIL=1 P42_SIGS=14:73 P42_SIGSX=14:73 P42_LEVELS=1 $BIN breakout tick --cache /data/p42-t566/cache --trades /data/aggtrades/binance --variants $W/variants-$NAME.txt --symbols "\$(cat /data/p42-t566/symbols.txt)" --out $W/out/$NAME --threads $TH > $W/$NAME.log 2>&1
rc=\$?
mkdir -p $W/out/$NAME
echo \$rc > $W/out/$NAME/DONE
EOS
UNIT="p42-t588-$NAME"
ssh "${K[@]}" "$H" "systemctl reset-failed $UNIT 2>/dev/null; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $W/run-$NAME.sh >/dev/null 2>&1 && echo started $UNIT"
