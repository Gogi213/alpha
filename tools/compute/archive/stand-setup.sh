#!/bin/bash
# stand-setup.sh: стенд малых гейтов в памяти /dev/shm/alpha-stand — сутки 2026-01-15 и 2026-01-01 всех символов + довесок D+1 (01-16, 01-02), касания D20, regime. Читает с диска один раз; прогоны — stand.sh (без замка).
E=/data/tk046/jan/home/alpha/epochs/e-jan; ST=/dev/shm/alpha-stand
av=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); echo "MemAvailable_GB $av"; [ "$av" -ge 38 ] || { echo "мало памяти (нужно >= 38 ГБ до копии: ~11 ГБ + 25 ГБ под волны)"; exit 1; }
rm -rf $ST; mkdir -p $ST/study/approaches/D20 $ST/root $ST/bin
for d in 2026-01-15 2026-01-01; do
  mkdir -p $ST/study/root-$d $ST/study/approaches/D20/$d
  ls $E/study/root-$d | xargs -P 6 -I{} ionice -c3 nice -n 19 cp -pL $E/study/root-$d/{} $ST/study/root-$d/{}
  ionice -c3 nice -n 19 cp -rpL $E/study/approaches/D20/$d/. $ST/study/approaches/D20/$d/
  echo "$d ok $(date +%T)"
done
cp -rpL $E/study/regime $ST/study/regime
for d in 2026-01-16 2026-01-02; do ls $E/root | grep -- "-$d" | xargs -P 6 -I{} ionice -c3 nice -n 19 cp -pL $E/root/{} $ST/root/{}; echo "carry $d ok $(date +%T)"; done
ls $E/root | grep -v binlog | xargs -I{} cp -pL $E/root/{} $ST/root/{}
du -sh $ST | tail -1; touch $ST/.ready
