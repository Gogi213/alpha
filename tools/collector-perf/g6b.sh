#!/bin/bash
# G6b: финальный кандидат против прежнего бинаря бок о бок, по 2 шарда на биржу, 4 биржи одновременно; больше на srv ничего.
# Сначала 5 мин фона боя (только монитор), затем рук на окно W секунд (по умолчанию 3600). Результаты — /data/perf/g6b-<биржа>.txt; монитор — g6b-mon.txt, g6b-arms.tsv.
# NEWBIN, W_BINANCE (окно реактора мкс для Binance), W_OTHER (для остальных), PRIO (свойства systemd обеих рук; по умолчанию пусто).
cd /data/perf
W=${1:-3600}
NEWBIN=${NEWBIN:-/data/perf/alpha-final4}
W_BINANCE=${W_BINANCE:-5000}
W_OTHER=${W_OTHER:-10000}
PRIO=${PRIO:-}   # свойства systemd — одинаковые у обеих рук (равные условия)
rm -f g6b.done g6b-mon.txt g6b-arms.tsv
for e in binance bybit okx bingx; do
  extra=""; wr=$W_OTHER
  if [ $e = binance ]; then extra="--never-slow --lazy"; wr=$W_BINANCE; fi
  printf "A-old|/data/perf/alpha-base3|%s|%s\nNEW|%s|%s --reactor --worker-inline --reactor-window-us %s --rx-stamp kernel|%s\n" "$extra" "$PRIO" "$NEWBIN" "$extra" "$wr" "$PRIO" > arms-g6b-$e.txt
done
nohup python3 g6b-mon.py 300 > /dev/null 2>&1 &
sleep 300
[ -f g6b.done ] && exit 1
PIDS=()
./shadow.sh binance $W book00,mark00 arms-g6b-binance.txt > g6b-binance.txt 2>&1 & PIDS+=($!)
./shadow.sh bybit $W book00,trade00 arms-g6b-bybit.txt > g6b-bybit.txt 2>&1 & PIDS+=($!)
./shadow.sh okx $W book00,trade00 arms-g6b-okx.txt > g6b-okx.txt 2>&1 & PIDS+=($!)
./shadow.sh bingx $W book00,trade00 arms-g6b-bingx.txt > g6b-bingx.txt 2>&1 & PIDS+=($!)
wait "${PIDS[@]}"   # только шадоу-прогоны: монитор (фон) ждёт g6b.done и сам бы не дал wait вернуться
[ -f g6b.done ] || echo DONE > g6b.done
