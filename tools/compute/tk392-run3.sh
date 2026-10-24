#!/bin/bash
# TK-392: слой «квота WBNB/WETH» (v3, нога USD = цена BNB/ETH по сделкам Gate спот). середины -> события/отклик. NX ядер (15).
set -e
cd /data/tk0380; O=tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python
mkdir -p $O/parts3 $O/mid3 $O/curve3
python3 tk392-pick2.py $O/pick2.csv
for ch in bsc ethereum base; do
  awk -F, -v c=$ch 'NR>1&&$2==c{print $3}' $O/pick2.csv > $O/pools2-$ch.txt
  [ -s $O/pools2-$ch.txt ] || continue
  ls sqd/$ch/part-*.jsonl | xargs -P $NX -I{} sh -c 'b=$(basename {} .jsonl); [ -s '$O'/parts3/'$ch'-$b.csv ] || { grep -h -F -i -f '$O'/pools2-'$ch'.txt {} | python3 tk392-mid-multi.py '$O'/pick2.csv '$ch' > '$O'/parts3/'$ch'-$b.csv.tmp && mv '$O'/parts3/'$ch'-$b.csv.tmp '$O'/parts3/'$ch'-$b.csv; }'
done
python3 - <<'Q'
import glob, gzip, collections
rows = collections.defaultdict(list)
for fn in glob.glob('/data/tk0380/tk392/parts3/*.csv'):
    for l in open(fn):
        c, ts, b, i, m = l.rstrip().split(','); rows[c].append((int(ts), int(b), int(i), m))
for c, r in rows.items():
    r.sort()
    with gzip.open(f'/data/tk0380/tk392/mid3/{c}.csv.gz', 'wt') as f:
        for ts, b, i, m in r: f.write(f'{ts},{b},{i},{m}\n')
print(len(rows), 'coins with mids')
Q
awk -F, 'NR>1{print $1, $7}' $O/pick2.csv | while read c leg; do [ -s $O/mid3/$c.csv.gz ] && echo "$c $leg"; done > $O/list3.txt
xargs -P $NX -L1 sh -c '[ -s '$O'/curve3/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid3/$0.csv.gz '$O'/curve3/$0 $1 > '$O'/curve3/$0.log 2>&1' < $O/list3.txt
echo DONE > $O/run3.done
