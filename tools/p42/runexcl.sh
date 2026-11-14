#!/usr/bin/env bash
# П-42 §13 (приказ владельца 14.11 про аутсайдеров): проверка исключением на 1м — база (variants-base.txt) на всех монетах, кроме списка `excl-*.txt`.
# Каждый список — один прогон `breakout run` на srv (под systemd-run: Nice=19, CPUWeight=1, IO idle) → <локально>/excl/<имя>/b0/.
# Использование: [P42_THREADS=4] bash tools/p42/runexcl.sh [бинарь на srv]
set -euo pipefail
SRV="${P42_SRV:-/data/p42v2}"; LOC="${P42_LOCAL:-data/p42v2}"; BIN="${1:-$SRV/bin/alpha-p42-v7}"; TH="${P42_THREADS:-4}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
ssh "${K[@]}" "$H" "rm -rf $SRV/excl_in $SRV/out/excl; mkdir -p $SRV/excl_in"
scp -q "${K[@]}" "$LOC"/excl-*.txt "$H:$SRV/excl_in/"; scp -q "${K[@]}" "$LOC/variants-base.txt" "$H:$SRV/"
cat > /tmp/p42-runexcl-remote.sh <<EOS
#!/usr/bin/env bash
for f in $SRV/excl_in/excl-*.txt; do
  n=\$(basename \$f .txt)
  $BIN breakout run --cache $SRV/cache --variants $SRV/variants-base.txt --symbols "\$(cat \$f)" --threads $TH --out $SRV/out/excl/\$n > /tmp/p42-\$n.log 2>&1 || echo "FAIL \$n"
done
EOS
scp -q "${K[@]}" /tmp/p42-runexcl-remote.sh "$H:$SRV/runexcl-remote.sh"
UNIT="p42-excl-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/runexcl-remote.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 1; done; ls $SRV/out/excl | wc -l"
rm -rf "$LOC/excl"; mkdir -p "$LOC/excl"
ssh "${K[@]}" "$H" "tar -C $SRV/out -czf - excl" | tar -m -C "$LOC" -xzf -
echo "pulled: $(ls $LOC/excl | wc -l) lists"
