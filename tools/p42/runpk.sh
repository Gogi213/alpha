#!/usr/bin/env bash
# П-42 §13.11: `b0` (гейт: pk=0 на v13 побайтово как прежний b0) и `b0pk` (pk=1) на тиках 4 мс, вселенная 544|566, на srv под systemd-run
# (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO). Результат остаётся на srv: /data/p42-t<U>/out/pk/{b0,b0pk}; P42_DETAIL=1 пишет `<side>-detail.jsonl`.
# Использование: bash tools/p42/runpk.sh 544|566 [бинарь на srv]
set -euo pipefail
U="${1:?544|566}"; BIN="${2:-/data/p42v2/bin/alpha-p42-v13}"; SRV=/data/p42-t$U; TH=6; NAME=pk
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
ssh "${K[@]}" "$H" "cat > $SRV/variants-$NAME.txt" <<EOS
b0 lat=4 latx=4
b0pk lat=4 latx=4 pk=1
EOS
ssh "${K[@]}" "$H" "cat > $SRV/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/$NAME
P42_DETAIL=1 $BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt --symbols "\$(cat $SRV/symbols.txt)" --out $SRV/out/$NAME --threads $TH > /tmp/p42-t$U-$NAME.log 2>&1 || echo "FAIL $NAME"
EOS
UNIT="p42-t$U-$NAME-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/run-$NAME.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 5; done; grep -E 'pk \(|фаза|готово|ОШИБК|error|FAIL' /tmp/p42-t$U-$NAME.log | cut -c1-240 | tail -8"
