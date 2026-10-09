#!/bin/bash
# chain83 (TK-048/TK-053 07.10): chain82-B + (а) tk048-sample3.sh (чтения/записи/сектора по sdb и sda раз в секунду) + (б) РЕБАЛАНС: бинлоги волны с sdb копируются на sda (/alpha-sda/tk048/<мес>/), симлинки в корнях root-<сутки> перенацеливаются атомарно; карта «ссылка старое новое» — /data/tk048/rebal-c83.map (откат: ln -sfn старое ссылка). Копии и шм — «подготовка данных», в wall не входят.
# Запуск: systemd-run --unit tk048-chain83 --collect bash /data/tk048/chain83.sh ; выход /data/tk048/chain83.txt, маркер chain83.done.
rm -f /data/tk048/chain83.done /data/tk048/chain83.txt
OUT=/data/tk048/chain83.txt; MAP=/data/tk048/rebal-c83.map; : > $MAP
t0=$(date +%s); moved=0; mb=0; bad=0
while read -r sy d; do
  case ${d:5:2} in 01) MON=jan;; 02) MON=feb;; *) continue;; esac
  R=/data/tk046/$MON/home/alpha/epochs/e-$MON/study/root-$d
  for f in $R/$sy-$d.binlog*; do
    [ -L "$f" ] || continue
    case "$f" in *.events) continue;; esac
    t=$(readlink -f "$f"); case "$t" in /alpha-sda/*) continue;; esac
    dst=/alpha-sda/tk048/$MON/$(basename "$t")
    if [ ! -e "$dst" ] || [ "$(stat -c %s "$dst")" != "$(stat -c %s "$t")" ]; then
      nice -n 10 ionice -c2 -n7 cp "$t" "$dst.part" && [ "$(stat -c %s "$dst.part")" = "$(stat -c %s "$t")" ] && mv -f "$dst.part" "$dst" || { bad=$((bad+1)); rm -f "$dst.part"; continue; }
    fi
    ln -s "$dst" "$f.c83new" && mv -T "$f.c83new" "$f" && { echo "$f $t $dst" >> $MAP; moved=$((moved+1)); mb=$((mb+$(stat -c %s "$dst")/1048576)); }
  done
done < /data/tk048/pflist-jf59.txt
{ echo "rebal_s $(( $(date +%s)-t0 )) moved $moved mb $mb bad $bad"; } >> $OUT
if [ "$bad" -gt 0 ] || [ "$moved" -gt 1000 ]; then echo "ABORT ребаланс: bad $bad moved $moved" >> $OUT; touch /data/tk048/chain83.done; exit 1; fi
left=0; while read -r sy d; do case ${d:5:2} in 01) MON=jan;; 02) MON=feb;; *) continue;; esac
  for f in /data/tk046/$MON/home/alpha/epochs/e-$MON/study/root-$d/$sy-$d.binlog; do [ -e "$f" ] && case "$(readlink -f "$f")" in /alpha-sda/*) ;; *) left=$((left+1));; esac; done
done < /data/tk048/pflist-jf59.txt
echo "binlogs_not_on_sda $left" >> $OUT
SHA=/dev/shm/abin-t46m
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); smb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_df $(df -m /dev/shm | tail -1)"; echo "shm_n_files $n shm_mb $smb"; } >> $OUT
if [ "$n" -lt 5000 ] || [ "$smb" -lt 2000 ] || [ "$smb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> $OUT; rm -rf $SHA; touch /data/tk048/chain83.done; exit 1; fi
sed 's#/data/tk048/tk048-sample.sh#/data/tk048/tk048-sample3.sh#' /data/tk048/tk048-orch-grp16t.sh > /data/tk048/tk048-orch-grp16t-s3.sh
grep -q tk048-sample3.sh /data/tk048/tk048-orch-grp16t-s3.sh || { echo "ABORT подмена сэмплера не сработала" >> $OUT; touch /data/tk048/chain83.done; exit 1; }
cat > /data/tk048/chain83-inner.sh <<'IN'
#!/bin/bash
systemd-run --wait --collect --unit tk048-c83 -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t-s3.sh c83 alpha-b18pgoflag 8 20 > /dev/null 2>&1
{ echo "== c83 alpha-b18pgoflag shm-abin + sample3 + rebalance"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c83.out/metrics.txt; } >> /data/tk048/chain83.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain83-inner.sh > /dev/null 2>&1
rm -rf $SHA
touch /data/tk048/chain83.done
