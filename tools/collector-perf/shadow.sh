#!/bin/bash
# G-022 collector-perf: «бок о бок» (G6) — несколько процессов (рук) на одном подмножестве шардов биржи одновременно, одинаковые условия.
# Запуск на srv: shadow.sh <биржа> <секунд окна> <шарды через запятую> <файл рук>
# Файл рук: по строке `метка|бинарь|доп. аргументы` (первая рука — эталон для `collect compare` и сравнения ЦП).
# Меряет: ЦП процесса за окно после прогрева 20 с (user/sys, ядер), переключения контекста, кадры за окно; затем `collect compare` вывода каждой руки с эталоном.
set -u
EX=$1; WIN=$2; SHARDS=$3; ARMS=$4
O=/data/perf/sh; mkdir -p $O
case $EX in   # свежий файл пула: после полуночи UTC файла нового дня может ещё не быть (его пишет запуск боевого процесса) — берётся последний
  binance) D=/data/collect/binance/data ;;
  bybit)   D=/data/turbo-probe/soak-bybit/data ;;
  okx)     D=/data/collect/okx/data ;;
  bingx)   D=/data/collect/bingx/data ;;
  *) echo "биржа?"; exit 2 ;;
esac
POOL=$(ls -t $D/pool-*.json | head -1)
TAG=$(date -u +%H%M%S)
R=$O/$EX-$TAG; mkdir -p $R
LABELS=(); BINS=()
while IFS='|' read -r label bin flags props; do
  [ -z "$label" ] && continue
  LABELS+=("$label"); BINS+=("$bin")
  mkdir -p $R/$label/data
  systemd-run --unit=shadow-$EX-$label-$TAG --collect -p CPUAccounting=yes $props $bin collect run --exchange $EX --root $R/$label/data --health $R/$label/health.json \
    --pool-file $POOL --only-shards $SHARDS --lanes 2 --duration-s $((WIN + 25)) --connect-budget 40 $flags > $R/$label/unit.txt 2>&1
done < $ARMS
sleep 20
pid() { systemctl show -p MainPID --value shadow-$EX-$1-$TAG; }
snap() { # метка тег
  p=$(pid $1)
  [ "$p" = 0 ] && return
  awk '{print $14, $15}' /proc/$p/stat > $R/$1.$2.cpu
  cat /proc/$p/task/*/status 2>/dev/null | awk '/^voluntary_ctxt_switches/{v+=$2} /^nonvoluntary_ctxt_switches/{n+=$2} END{print v, n}' > $R/$1.$2.ctx
  ls /proc/$p/task | wc -l > $R/$1.$2.thr
  date +%s.%N > $R/$1.$2.t
}
for l in "${LABELS[@]}"; do snap $l a; done
sleep $WIN
for l in "${LABELS[@]}"; do snap $l b; done
sleep 12
for l in "${LABELS[@]}"; do
  python3 - "$R" "$l" <<'PY'
import sys, glob, json
R, v = sys.argv[1], sys.argv[2]
def rd(n): return open(f"{R}/{v}.{n}").read().split()
try:
    ua, sa = map(int, rd("a.cpu")); ub, sb = map(int, rd("b.cpu"))
    ca = list(map(int, rd("a.ctx"))); cb = list(map(int, rd("b.ctx")))
    dt = float(rd("b.t")[0]) - float(rd("a.t")[0]); thr = rd("b.thr")[0]
except Exception as e:
    print(v, "нет снимков", e); sys.exit()
frames = 0
for f in glob.glob(f"{R}/{v}/data/stats-*.jsonl"):
    for ln in open(f):
        try: frames += json.loads(ln)["msgs"]
        except Exception: pass
print(f"{v:10s} окно {dt:.0f} с: ЦП {(ub-ua+sb-sa)/100/dt:.3f} ядра (user {(ub-ua)/100/dt:.3f} + sys {(sb-sa)/100/dt:.3f}); переключений {(cb[0]+cb[1]-ca[0]-ca[1])/dt:,.0f}/с; потоков {thr}; кадров за жизнь {frames}")
PY
done
FIRST=${LABELS[0]}; FBIN=${BINS[0]}
for i in "${!LABELS[@]}"; do
  [ $i = 0 ] && continue
  echo "--- compare ${LABELS[0]} vs ${LABELS[$i]}"
  ${BINS[$i]} collect compare --a $R/${LABELS[0]}/data --b $R/${LABELS[$i]}/data
done
echo "каталог: $R"
for l in "${LABELS[@]}"; do
  python3 -c "
import json
h = json.load(open('$R/$l/health.json'))
print('$l', 'reactor', json.dumps(h.get('reactor')), '| queue_lag', h['backpressure']['queue_lag_ms'], '| lag_out', h['lag_out_ms'])
"
done
