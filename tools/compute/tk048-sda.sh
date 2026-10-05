#!/bin/bash
# tk048-sda.sh: копия части суток янв+фев на sda (/alpha-sda/tk048) и перепривязка ссылок корней TK-046 (В-184). Оригиналы /data (sdb) не трогаются.
# Раскладка: сутки по порядку, каждые — на диск с меньшей суммой байт (ровно поровну по байтам, соседние сутки чередуются).
# Идемпотентно: rsync -t пропускает файл с тем же размером и mtime ⇒ повтор после правки данных докопирует изменённое.
# Откат: tk048-sda.sh --revert (ссылки обратно по /data/tk048/sda-map.tsv). Запуск: systemd-run --unit=tk048-sda --property=CPUQuota=100% bash tk048-sda.sh
D=/alpha-sda/tk048; M=/data/tk048/sda-map.tsv; mkdir -p $D /data/tk048
if [ "$1" = "--revert" ]; then while IFS=$'\t' read -r link old new; do [ -e "$old" ] && ln -sfn "$old" "$link"; done < $M; exit 0; fi
: > $M.plan
for mon in jan feb; do R=/data/tk046/$mon/home/alpha/epochs/e-$mon/root; mkdir -p $D/$mon
  for l in $R/*.binlog; do [ -L "$l" ] || continue; real=$(readlink -f "$l"); echo -e "$(basename $l | grep -o '20[0-9-]*[0-9]')\t$l\t$real\t$(stat -c %s "$real")\t$mon"; done
done | sort -k1,1 -s > $M.all
awk -F'\t' 'BEGIN{OFS="\t"} {sz[$1]+=$4; rows[$1]=rows[$1] "\n" $0; if(!($1 in seen)){seen[$1]=1; days[++n]=$1}}
 END{a=0;b=0; for(i=1;i<=n;i++){d=days[i]; if(a<=b){side="sda";a+=sz[d]}else{side="sdb";b+=sz[d]}; print d,side,sz[d] > "/dev/stderr"; split(rows[d],r,"\n"); for(j=2;j<=length(r);j++){print side,r[j]}}}' $M.all > $M.plan 2> $M.days
: > $M
grep -c . $M.plan; awk -F'\t' '$1=="sda"{s+=$5} $1=="sdb"{t+=$5} END{print "sda_GB",s/1e9,"sdb_GB",t/1e9}' $M.plan
awk -F'\t' '$1=="sda"{print $3"\t"$4"\t"$6}' $M.plan | while IFS=$'\t' read -r link real mon; do
  new=$D/$mon/$(basename "$real")
  nice -n 10 ionice -c2 -n6 rsync -t "$real" "$new" || { echo "FAIL $real" >&2; continue; }
  [ "$(stat -c %s "$real")" = "$(stat -c %s "$new")" ] || { echo "SIZE $real" >&2; continue; }
  echo -e "$link\t$real\t$new" >> $M; ln -sfn "$new" "$link"
done
echo done > /data/tk048/sda.done
