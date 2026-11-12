#!/bin/bash
# TK-392 боевой проход (слой «истинная середина»: v3, стейбл-квота): середины -> события/отклик по монетам. NX ядер (15).
set -e
cd /data/tk0380; O=tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python
mkdir -p $O/parts $O/mid $O/curve
for ch in bsc ethereum base; do
  cut -d, -f3 <(awk -F, -v c=$ch 'NR==1||$2==c' $O/pick.csv) | tail -n +2 > $O/pools-$ch.txt
  ls sqd/$ch/part-*.jsonl | xargs -P $NX -I{} sh -c 'b=$(basename {} .jsonl); [ -s '$O'/parts/'$ch'-$b.csv ] || { grep -h -F -i -f '$O'/pools-'$ch'.txt {} | python3 tk392-mid-multi.py '$O'/pick.csv '$ch' > '$O'/parts/'$ch'-$b.csv.tmp && mv '$O'/parts/'$ch'-$b.csv.tmp '$O'/parts/'$ch'-$b.csv; }'
done
python3 - <<'P'
import glob, gzip, collections
rows = collections.defaultdict(list)
for fn in glob.glob('/data/tk0380/tk392/parts/*.csv'):
    for l in open(fn):
        c, ts, b, i, m = l.rstrip().split(','); rows[c].append((int(ts), int(b), int(i), m))
for c, r in rows.items():
    r.sort()
    with gzip.open(f'/data/tk0380/tk392/mid/{c}.csv.gz', 'wt') as f:
        for ts, b, i, m in r: f.write(f'{ts},{b},{i},{m}\n')
print(len(rows), 'coins with mids')
P
ls $O/mid | sed 's/.csv.gz//' | xargs -P $NX -I{} sh -c '[ -s '$O'/curve/{}-curve.csv ] || '$PY' tk392-event.py {} '$O'/mid/{}.csv.gz '$O'/curve/{} > '$O'/curve/{}.log 2>&1'
echo DONE > $O/run.done
