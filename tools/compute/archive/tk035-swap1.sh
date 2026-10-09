#!/bin/bash
# swap1.sh SRCFILE — подмена одного бинлога: копия рядом, sha, старый -> /data/alpha-oldtick, новый на место
f=$1; b=${f##*/}; mon=${b#*-2026-}; mon=${mon%%-*}
case $mon in 01) m=jan;; 02) m=feb;; 03) m=mar;; 04) m=apr;; 05) m=may;; 06) m=jun;; 07) m=jul;; 08) m=aug;; *) echo "BADMON $b"; exit 1;; esac
D=/data/alpha/epochs/e-$m/root; O=/data/alpha-oldtick/e-$m/root
[ -f $D/$b ] || { echo "NOOLD $b"; exit 1; }
mkdir -p $O
cp $f $D/.$b.new || { echo "CPFAIL $b"; exit 1; }
s1=$(sha256sum < $f | cut -d" " -f1); s2=$(sha256sum < $D/.$b.new | cut -d" " -f1)
[ "$s1" = "$s2" ] || { echo "SHAFAIL $b"; exit 1; }
mv -n $D/$b $O/$b && mv $D/.$b.new $D/$b && echo "$s1  alpha/epochs/e-$m/root/$b" >> /root/tk035new/swap-manifest.part.$$ && echo "OK $b"
