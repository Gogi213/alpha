#!/usr/bin/env bash
# П-42 §13.4 п. 9: точный пересчёт без 22 монет (`--symbols` = 544) на srv — минутки (`variants-excl22-1m.txt`) и тики 4 мс (`variants-excl22-tick.txt`),
# контроль — `b0` / `b0_lx4` на полном списке (566) → <локально>/{excl22-1m,excl22-tick,ctrl-1m,ctrl-tick}/.
# Использование: [P42_THREADS=6] bash tools/p42/runexcl22.sh [бинарь на srv]
set -euo pipefail
SRV="${P42_SRV:-/data/p42v2}"; LOC="${P42_LOCAL:-data/p42v2}"; BIN="${1:-$SRV/bin/alpha-p42-v11}"; TH="${P42_THREADS:-6}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
scp -q "${K[@]}" "$LOC"/variants-excl22-1m.txt "$LOC"/variants-excl22-tick.txt "$LOC"/symbols-excl22.txt "$LOC"/symbols-full.txt "$LOC"/variants-base.txt "$LOC"/variants-tickbase.txt "$H:$SRV/"
cat > /tmp/p42-runexcl22-remote.sh <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/excl22-1m out/excl22-tick out/ctrl-1m out/ctrl-tick
$BIN breakout run --cache $SRV/cache --variants $SRV/variants-excl22-1m.txt --symbols "\$(cat $SRV/symbols-excl22.txt)" --threads $TH --out $SRV/out/excl22-1m > /tmp/p42-excl22-1m.log 2>&1 || echo "FAIL excl22-1m"
$BIN breakout run --cache $SRV/cache --variants $SRV/variants-base.txt --symbols "\$(cat $SRV/symbols-full.txt)" --threads $TH --out $SRV/out/ctrl-1m > /tmp/p42-ctrl-1m.log 2>&1 || echo "FAIL ctrl-1m"
$BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-excl22-tick.txt --symbols "\$(cat $SRV/symbols-excl22.txt)" --out $SRV/out/excl22-tick --threads $TH > /tmp/p42-excl22-tick.log 2>&1 || echo "FAIL excl22-tick"
grep -h 'b0_lx4' $SRV/variants-excl22-tick.txt > $SRV/variants-ctrl-tick.txt
$BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-ctrl-tick.txt --symbols "\$(cat $SRV/symbols-full.txt)" --out $SRV/out/ctrl-tick --threads $TH > /tmp/p42-ctrl-tick.log 2>&1 || echo "FAIL ctrl-tick"
EOS
scp -q "${K[@]}" /tmp/p42-runexcl22-remote.sh "$H:$SRV/runexcl22-remote.sh"
UNIT="p42-excl22-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/runexcl22-remote.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 2; done; ls $SRV/out | grep -E 'excl22|ctrl'; tail -n 2 /tmp/p42-excl22-1m.log /tmp/p42-excl22-tick.log | cut -c1-200"
for n in excl22-1m excl22-tick ctrl-1m ctrl-tick; do
  rm -rf "$LOC/$n"
  ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - $n" | tar -m -C "$LOC" -xzf -
done
echo "pulled: $(ls $LOC/excl22-1m | wc -l) / $(ls $LOC/excl22-tick | wc -l) / $(ls $LOC/ctrl-1m | wc -l) / $(ls $LOC/ctrl-tick | wc -l)"
