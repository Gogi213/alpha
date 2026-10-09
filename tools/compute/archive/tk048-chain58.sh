#!/bin/bash
# chain58: пере-привязка blk56.data с полным списком файлов e-jan (+ вердикт TK-044), классы с признаком «в окне суток 1–15» (-in/-out); выход chain58-map.txt. Под замком stand (диск).
rm -f /data/tk048/chain58.done
cat > /data/tk048/chain58-inner.sh <<'IN'
#!/bin/bash
E=/data/tk046/jan/home/alpha/epochs/e-jan
find -L $E -type f -not -path "$E/b5*" -not -path "$E/b5-*" > /data/tk048/chain58-files.txt 2>/dev/null
ls /data/tk044/final3/verdict.csv >> /data/tk048/chain58-files.txt
perf script -i /data/tk048/blk56.data 2>/dev/null | python3 /data/tk048/tk048-blkmap2.py /data/tk048/chain58-files.txt > /data/tk048/chain58-map.txt 2>&1
IN
chmod +x /data/tk048/chain58-inner.sh
/data/tk052/benchrun2.sh stand bash /data/tk048/chain58-inner.sh > /dev/null 2>&1
touch /data/tk048/chain58.done
