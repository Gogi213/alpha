#!/bin/bash
# A8 «меньше соединений, больше потоков на каждое» (collector-perf, 14.11): руки — один бинарь, разные `--streams-per-conn` и шарды, покрывающие ОДНИ И ТЕ ЖЕ символы.
# usage: a8.sh <биржа> <секунд окна> <файл рук>; файл рук: `метка|бинарь|аргументы` (в аргументы входят `--only-shards …` и `--streams-per-conn …`).
# Меряет за окно (после прогрева 25 с): соединений, кадров/с (счётчик реактора), пакетов/с и байт/с (`ss -i`: segs_in, bytes_received по сокетам процесса), пакетов на кадр, ЦП процесса
# (ядер и мкс на кадр), чтений и системных вызовов чтения на кадр; затем `collect compare` рук (покрытие и тождество личностей). Итог — таблица в stdout.
set -u
EX=$1; WIN=$2; ARMS=$3
O=/data/perf/a8; mkdir -p $O
case $EX in
  binance) D=/data/collect/binance/data ;;
  bybit) D=/data/turbo-probe/soak-bybit/data ;;
  okx) D=/data/collect/okx/data ;;
  bingx) D=/data/collect/bingx/data ;;
  *) echo "биржа?"; exit 2 ;;
esac
POOL=$(ls -t $D/pool-*.json | head -1)
TAG=$(date -u +%H%M%S); R=$O/$EX-$TAG; mkdir -p $R
LABELS=(); BINS=()
while IFS='|' read -r label bin flags; do
  [ -z "$label" ] && continue
  LABELS+=("$label"); BINS+=("$bin"); mkdir -p $R/$label/data
  systemd-run --unit=a8-$EX-$label-$TAG --collect -p CPUAccounting=yes $bin collect run --exchange $EX --root $R/$label/data --health $R/$label/health.json \
    --pool-file $POOL --lanes 2 --duration-s $((WIN + 60)) --connect-budget 40 $flags > $R/$label/unit.txt 2>&1
done < $ARMS
sleep 25
snap() { # тег
  for l in "${LABELS[@]}"; do
    p=$(systemctl show -p MainPID --value a8-$EX-$l-$TAG)
    [ "$p" = 0 ] && continue
    awk '{print $14+$15}' /proc/$p/stat > $R/$l.$1.cpu
    ss -tnHi -p state established 2>/dev/null | python3 -c "
import re, sys
pid = '$p'; segs = byt = conns = 0
lines = sys.stdin.read().split('\n')
i = 0
while i < len(lines) - 1:
    head, info = lines[i], lines[i + 1]
    if 'pid=' + pid + ',' in head:
        conns += 1
        m = re.search(r'segs_in:(\d+)', info); segs += int(m.group(1)) if m else 0
        m = re.search(r'bytes_received:(\d+)', info); byt += int(m.group(1)) if m else 0
    i += 2
print(conns, segs, byt)" > $R/$l.$1.ss
    python3 -c "
import json
h = json.load(open('$R/$l/health.json')); r = h.get('reactor') or {}
print(r.get('frames', 0), r.get('reads', 0), r.get('read_syscalls', 0))" > $R/$l.$1.rx
    date +%s.%N > $R/$l.$1.t
  done
}
snap a
sleep $WIN
snap b
sleep 3
python3 - $R "${LABELS[@]}" <<'PY'
import sys
R, labels = sys.argv[1], sys.argv[2:]
def rd(n): return open(f"{R}/{n}").read().split()
print(f"{'рука':8s} {'соед.':>5s} {'кадр/с':>8s} {'пак/с':>8s} {'МБ/с':>6s} {'пак/кадр':>9s} {'Б/пак':>6s} {'ЦП ядер':>8s} {'мкс/кадр':>9s} {'чт/кадр':>8s} {'вызовов/кадр':>13s}")
for l in labels:
    try:
        ca, cb = int(rd(f"{l}.a.cpu")[0]), int(rd(f"{l}.b.cpu")[0])
        sa, sb = list(map(int, rd(f"{l}.a.ss"))), list(map(int, rd(f"{l}.b.ss")))
        xa, xb = list(map(int, rd(f"{l}.a.rx"))), list(map(int, rd(f"{l}.b.rx")))
        dt = float(rd(f"{l}.b.t")[0]) - float(rd(f"{l}.a.t")[0])
    except Exception as e:
        print(l, "нет снимков", e); continue
    fr, pk, by = (xb[0] - xa[0]) / dt, (sb[1] - sa[1]) / dt, (sb[2] - sa[2]) / dt
    cpu = (cb - ca) / 100 / dt
    print(f"{l:8s} {sb[0]:5d} {fr:8.0f} {pk:8.0f} {by/1e6:6.2f} {pk/max(fr,1):9.3f} {by/max(pk,1):6.0f} {cpu:8.3f} {cpu/max(fr,1)*1e6:9.1f} {(xb[1]-xa[1])/dt/max(fr,1):8.3f} {(xb[2]-xa[2])/dt/max(fr,1):13.3f}")
PY
for i in "${!LABELS[@]}"; do
  [ $i = 0 ] && continue
  echo "--- compare ${LABELS[0]} vs ${LABELS[$i]}"
  ${BINS[$i]} collect compare --a $R/${LABELS[0]}/data --b $R/${LABELS[$i]}/data 2>&1 | head -4 | cut -c1-240
done
echo "каталог: $R"
