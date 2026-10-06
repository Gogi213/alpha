#!/bin/bash
# chain60: сводный поток чтения sdb при N параллельных потоках (холодный кэш) в зависимости от read_ahead_kb и mq-deadline fifo_batch. Те же 16 крупных бинлогов sdb (суток 1–15.01), поток i читает файлы i::N. Настройки возвращаются. Под замком stand. Выход /data/tk048/chain60.txt
rm -f /data/tk048/chain60.done
cat > /data/tk048/chain60-inner.sh <<'IN'
#!/bin/bash
OUT=/data/tk048/chain60.txt; : > $OUT
Q=/sys/block/sdb/queue
RA0=$(cat $Q/read_ahead_kb); FB0=$(cat $Q/iosched/fifo_batch)
trap "echo \$RA0 > $Q/read_ahead_kb; echo \$FB0 > $Q/iosched/fifo_batch" EXIT
for f in $(cat /data/tk048/chain59-files.txt); do r=$(readlink -f $f); case $r in /alpha-sda/*) ;; *) echo "$(stat -c %s $r) $r";; esac; done | sort -rn | head -16 > /data/tk048/chain60-list.txt
TOT=$(awk '{s+=$1} END{print s}' /data/tk048/chain60-list.txt); echo "files 16 bytes $TOT ra0 $RA0 fb0 $FB0" >> $OUT
for cfg in "65536 16" "65536 256" "4096 16" "4096 256" "512 16"; do set -- $cfg; echo $1 > $Q/read_ahead_kb; echo $2 > $Q/iosched/fifo_batch
  for N in 1 4 16; do
    sync; echo 3 > /proc/sys/vm/drop_caches; sleep 2
    t0=$(date +%s.%N)
    for ((i=0;i<N;i++)); do ( awk -v n=$N -v i=$i 'NR%n==i{print $2}' /data/tk048/chain60-list.txt | while read -r f; do dd if=$f of=/dev/null bs=1M 2>/dev/null; done ) & done; wait
    t1=$(date +%s.%N)
    echo "ra $1 fifo_batch $2 N $N wall $(echo "$t1-$t0" | bc) MBps $(echo "$TOT/1000000/($t1-$t0)" | bc)" >> $OUT
  done
done
IN
chmod +x /data/tk048/chain60-inner.sh
/data/tk052/benchrun2.sh stand bash /data/tk048/chain60-inner.sh > /dev/null 2>&1
touch /data/tk048/chain60.done
