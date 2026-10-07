#!/bin/bash
# chain62: потолок способа (2)/(3) — разбиение тел кадров на колонки; 2 файла суток 01.01 (по контейнерам chain61 L1) + zstd-19/xz на целый контейнер для сравнения. Выход /data/tk048/chain62.txt
O=/data/tk048/chain62.txt; : > $O; rm -f /data/tk048/chain62.done
for n in 10 30; do f=$(sed -n ${n}p /data/tk048/chain61/list.txt | cut -d' ' -f2); b=$(basename $f).zst
  echo "orig $(stat -L -c %s $f) L1 $(stat -c %s /data/tk048/chain61/L1/$b) L9 $(stat -c %s /data/tk048/chain61/L9/$b)" >> $O
  zstd -d -c --long=31 /data/tk048/chain61/L1/$b | zstd -19 --long=27 -c | wc -c | sed 's/^/ stream zstd19 /' >> $O
  python3 /data/tk048/colsplit.py /data/tk048/chain61/L1/$b >> $O 2>&1
done
touch /data/tk048/chain62.done
