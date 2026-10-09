#!/bin/bash
# chain56 (CEO 06.10 п.3): карта чтения дисков по классам файлов. Холодная волна q15 (янв 1–15, b14, G=8 P=15) под perf record block:block_rq_issue;
# затем filefrag-экстенты файлов суток 01–15.01 и привязка секторов (tk048-blkmap.py). Выход /data/tk048/chain56-map.txt, маркер chain56.done.
rm -f /data/tk048/chain56.done
cat > /data/tk048/chain56-inner.sh <<'IN'
#!/bin/bash
perf record -a -e block:block_rq_issue -o /data/tk048/blk56.data -- sleep 100000 > /data/tk048/chain56-perf.log 2>&1 & PP=$!
sleep 3
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q56 -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m bash /data/tk048/tk048-orch-grp11.sh q56 alpha-b14flag $D15 8 15 > /dev/null 2>&1
kill -INT $PP; sleep 3; wait $PP 2>/dev/null
E=/data/tk046/jan/home/alpha/epochs/e-jan/study
for d in $(seq -f "2026-01-%02g" 1 15); do find -L $E/approaches/D20/$d $E/root-$d -type f 2>/dev/null; echo $E/regime/$d.csv; done > /data/tk048/chain56-files.txt
find -L $E/sigma240 -type f >> /data/tk048/chain56-files.txt 2>/dev/null
perf script -i /data/tk048/blk56.data  2>/dev/null | python3 /data/tk048/tk048-blkmap.py /data/tk048/chain56-files.txt > /data/tk048/chain56-map.txt 2>&1
IN
chmod +x /data/tk048/chain56-inner.sh
/data/tk052/benchrun2.sh wave bash /data/tk048/chain56-inner.sh > /dev/null 2>&1
touch /data/tk048/chain56.done
