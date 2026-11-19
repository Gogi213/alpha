#!/bin/bash
# Повтор записанных кадров: прежний бинарь (base3) против финального (final5) на 16 шардах, 5 проходов, taskset 6,7 (одна пара ядер, оба бинаря по очереди);
# итог — $D/rpf/summary-<бинарь>.txt (ЦП писателя и диска на Мкадр, хеш разжатого содержимого, разбор нс/кадр, у BingX — разжатие нс/кадр). Решение координатора: гонять на Sofia (D=/opt/alpha-compute/perf CPUS=2,3), srv не трогать.
D=${D:-/data/perf}; CPUS=${CPUS:-6,7}   # каталог с cap/ и бинарями alpha-<имя>; пара ядер
cd $D; rm -rf rpf; mkdir -p rpf
for b in base3 final5; do
  {
    for ex in binance bybit okx bingx; do
      for f in cap/$ex/data/capture-*.tsv; do
        s=$(basename $f .tsv); s=${s#capture-}
        extra=""; [ $ex = bingx ] && extra="--gunzip"
        taskset -c $CPUS ./alpha-$b collect replay --exchange $ex --capture $f --out rpf/$b-$ex-$s --repeat 5 $extra 2>&1 | sed "s/^/$ex $s | /"
      done
    done
  } > rpf/summary-$b.txt 2>&1
done
echo done > rpf/DONE
