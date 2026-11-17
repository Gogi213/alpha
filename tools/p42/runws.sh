#!/usr/bin/env bash
# П-42 §13.13: опции `ap` (подход с правильной стороны) и `pb` (прокол убивает уровень на сделке) на тиках 4 мс, вселенная 544|566,
# на srv под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO). Результат остаётся на srv: /data/p42-t<U>/out/ws/<вариант>;
# P42_DETAIL=1 пишет `<сторона>-detail.jsonl` (при ap ≠ 0 — с полем `ws`).
# Варианты: b0, b0pk — ворота «байт в байт» против v13; b0lab (ap=2) — метка, сделки тождественны b0; b0ws, b0wspk — основное;
# только на 566: b0ap, b0pb (разложение), b0ws_pool0 (оценка потерянных входов подхода).
# Использование: bash tools/p42/runws.sh 544|566 [бинарь на srv]
set -euo pipefail
U="${1:?544|566}"; BIN="${2:-/data/p42v2/bin/alpha-p42-v15}"; SRV=/data/p42-t$U; TH=6; NAME=ws
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
EXTRA=""
if [ "$U" = 566 ]; then EXTRA=$'b0ap lat=4 latx=4 ap=1\nb0pb lat=4 latx=4 pb=1\nb0ws_pool0 lat=4 latx=4 ap=1 pb=1 natr_min=0 t1h=0'; fi
ssh "${K[@]}" "$H" "cat > $SRV/variants-$NAME.txt" <<EOS
b0 lat=4 latx=4
b0pk lat=4 latx=4 pk=1
b0lab lat=4 latx=4 ap=2
b0ws lat=4 latx=4 ap=1 pb=1
b0wspk lat=4 latx=4 ap=1 pb=1 pk=1
$EXTRA
EOS
ssh "${K[@]}" "$H" "cat > $SRV/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $SRV
rm -rf out/$NAME
P42_DETAIL=1 $BIN breakout tick --cache $SRV/cache --trades /data/aggtrades/binance --variants $SRV/variants-$NAME.txt --symbols "\$(cat $SRV/symbols.txt)" --out $SRV/out/$NAME --threads $TH > /tmp/p42-t$U-$NAME.log 2>&1 || echo "FAIL $NAME"
EOS
UNIT="p42-t$U-$NAME-$(date +%s)"
ssh "${K[@]}" "$H" "systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $SRV/run-$NAME.sh >/dev/null 2>&1; while systemctl is-active --quiet $UNIT; do sleep 5; done; grep -E 'pk \(|pb \(|фаза|готово|ОШИБК|error|FAIL' /tmp/p42-t$U-$NAME.log | cut -c1-240 | tail -8"
