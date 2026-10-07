#!/bin/bash
# chain59: фрагментация бинлогов суток 1–15.01 (filefrag: файлы/экстенты/размер по дискам) + сырая последовательная скорость дисков (dd direct, один большой бинлог на диск). Под замком stand. Выход /data/tk048/chain59.txt
rm -f /data/tk048/chain59.done
cat > /data/tk048/chain59-inner.sh <<'IN'
#!/bin/bash
E=/data/tk046/jan/home/alpha/epochs/e-jan/study
OUT=/data/tk048/chain59.txt; : > $OUT
for d in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do ls -d $E/root-2026-01-$d/*.binlog; done > /data/tk048/chain59-files.txt
python3 - <<'PY' >> $OUT
import subprocess, re, os, collections
fs = [l.strip() for l in open('/data/tk048/chain59-files.txt') if l.strip()]
agg = collections.defaultdict(lambda: [0, 0, 0, []])
for i in range(0, len(fs), 100):
    ch = fs[i:i+100]
    cur = None
    for f in ch:
        pass
    out = subprocess.run(['filefrag'] + [os.path.realpath(f) for f in ch], capture_output=True, text=True).stdout
    for l in out.split('\n'):
        m = re.match(r'(.+): (\d+) extents? found', l)
        if not m: continue
        p = m.group(1); n = int(m.group(2)); sz = os.path.getsize(p)
        d = 'sda' if p.startswith('/alpha-sda') else 'sdb'
        a = agg[d]; a[0] += 1; a[1] += n; a[2] += sz; a[3].append((sz / n, n, sz))
for d, a in agg.items():
    big = [x for x in a[3] if x[2] > 100e6]
    print(d, 'files', a[0], 'extents', a[1], 'GB', round(a[2] / 1e9, 2), 'avg_extent_MB', round(a[2] / a[1] / 1e6, 1),
          'files>100MB', len(big), 'their_avg_extent_MB', round(sum(x[2] for x in big) / max(1, sum(x[1] for x in big)) / 1e6, 1))
PY
for d in sda sdb; do
  if [ $d = sda ]; then f=$(ls -S /alpha-sda/tk048/jan/study/root-2026-01-0[13579]/*.binlog 2>/dev/null | head -1); else f=$(ls -S /data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-02/*.binlog | head -1); fi
  f=$(readlink -f $f); sz=$(stat -c %s $f)
  echo "dd $d $f $sz" >> $OUT
  dd if=$f of=/dev/null bs=4M iflag=direct 2>&1 | tail -1 >> $OUT
done
IN
chmod +x /data/tk048/chain59-inner.sh
/data/tk052/benchrun2.sh stand bash /data/tk048/chain59-inner.sh > /dev/null 2>&1
touch /data/tk048/chain59.done
