#!/bin/bash
# tk065-g1ab3.sh: A/B 10.03 (G1, флаги выкл.): r2m-a и r2m-trace ЧЕРЕЗ обёртку с ALPHA_APPROACH_BIN_DIR (как alpha-93287fd-flag) — разница UNI +10 мс из-за env, а не кода?
# Выход /data/tk065/g1ab3.res (последняя строка done).
d=2026-03-10; R=/data/tk065/g1ab3.res; : > $R
for n in r2m-a r2m-trace; do
  printf '#!/bin/bash\n: ${ALPHA_APPROACH_BIN_DIR:=/data/tk052/abin-jf}\nexport ALPHA_APPROACH_BIN_DIR\nexec /opt/alpha-compute/bin/%s "$@"\n' $n > /opt/alpha-compute/bin/alpha-$n-flag; chmod +x /opt/alpha-compute/bin/alpha-$n-flag
  sed "s#S=/data/tk065/g1/\$d#S=/data/tk065/g1/\$d-w$n#" /data/tk065/g1-day.sh > /data/tk065/g1-day-w$n.sh
  bash /data/tk065/g1-day-w$n.sh $d alpha-$n-flag
  S=/data/tk065/g1/$d-w$n; f=b5/p07a-h2-fr3/$d/t-bid-btc4h-q1/signals.csv
  echo "$n flag: $(tr '\n' ' ' < $S/res.txt)" >> $R
  echo "$n cell: $(diff $S/$f /data/tk048/tk040-b14/mar/$f | grep -c '^<')" >> $R
done
echo done >> $R
