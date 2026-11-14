#!/usr/bin/env bash
# П-42 §13.8: один этап перетитровки на 544 монетах на srv: variants-<name>.txt → `breakout run` (минутки) или `breakout tick` (тики) с `--symbols` = 544
# под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) → <локально>/<name>/ (для run) или tick-<name>/ (для tick).
# Использование: bash tools/p42/run544.sh run|tick <name> [бинарь на srv]
set -euo pipefail
KIND="${1:?run|tick}"; NAME="${2:?name}"; SRV=/data/p42-544; LOC=data/p42v2-544; BIN="${3:-/data/p42v2/bin/alpha-p42-v11}"; TH=6
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
ssh "${K[@]}" "$H" "mkdir -p $SRV/out; [ -e $SRV/cache ] || ln -s /data/p42v2/cache $SRV/cache"
scp -q "${K[@]}" "$LOC/variants-$NAME.txt" "$LOC/symbols.txt" "$H:$SRV/"
if [ "$KIND" = run ]; then OUTN="$NAME"; CMD="$BIN breakout run --cache $SRV/cache --variants $SRV/variants-$NAME.txt"; else OUTN="tick-$NAME"; CMD="$BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt"; fi
cat > /tmp/p42-544-$NAME.sh <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/$OUTN
$CMD --symbols "\$(cat $SRV/symbols.txt)" --threads $TH --out $SRV/out/$OUTN > /tmp/p42-544-$NAME.log 2>&1 || echo "FAIL $NAME"
EOS
scp -q "${K[@]}" /tmp/p42-544-$NAME.sh "$H:$SRV/run-$NAME.sh"
UNIT="p42-544-$NAME-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/run-$NAME.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 2; done; tail -n 2 /tmp/p42-544-$NAME.log | cut -c1-220"
rm -rf "$LOC/$OUTN"
ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - $OUTN" | tar -m -C "$LOC" -xzf -
echo "pulled: $(ls $LOC/$OUTN | wc -l) entries into $LOC/$OUTN"
