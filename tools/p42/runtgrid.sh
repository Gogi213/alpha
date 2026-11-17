#!/usr/bin/env bash
# П-42 §13.9: один этап тиковой сетки на srv: variants-<stage>.txt → `breakout tick` (--symbols = вселенная) под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) → <локально>/<stage>/.
# Использование: bash tools/p42/runtgrid.sh 544|566 <stage> [бинарь на srv]
set -euo pipefail
U="${1:?544|566}"; NAME="${2:?stage}"; SRV=/data/p42-t$U; LOC=data/p42v2-t$U; BIN="${3:-/data/p42v2/bin/alpha-p42-v11}"; TH=6
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
ssh "${K[@]}" "$H" "mkdir -p $SRV/out; [ -e $SRV/cache ] || ln -s /data/p42v2/cache $SRV/cache"
scp -q "${K[@]}" "$LOC/variants-$NAME.txt" "$LOC/symbols.txt" "$H:$SRV/"
cat > /tmp/p42-t$U-$NAME.sh <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/$NAME
$BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt --symbols "\$(cat $SRV/symbols.txt)" --out $SRV/out/$NAME --threads $TH > /tmp/p42-t$U-$NAME.log 2>&1 || echo "FAIL $NAME"
EOS
scp -q "${K[@]}" /tmp/p42-t$U-$NAME.sh "$H:$SRV/run-$NAME.sh"
UNIT="p42-t$U-$NAME-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/run-$NAME.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 2; done; tail -n 2 /tmp/p42-t$U-$NAME.log | cut -c1-220"
rm -rf "$LOC/$NAME"
ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - $NAME" | tar -m -C "$LOC" -xzf -
echo "pulled: $(ls $LOC/$NAME | wc -l) entries into $LOC/$NAME"
