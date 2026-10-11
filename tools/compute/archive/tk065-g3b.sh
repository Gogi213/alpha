#!/bin/bash
# tk065-g3b.sh: G3б — трасса решений R2 (бинарник r2m-trace, ALPHA_R2_TRACE=1) для 3 сделок на ADA 07.01:
# pyre3u1 и conv0a20 (набор r2), tsl10t2 (набор r2t). Корень суток — только ADAUSDT (трасса без метки символа).
# Выход: /data/tk065/g3b/{pyre3u1,conv0a20,tsl10t2}.trace (stderr грида), маркер /data/tk065/g3b.done
set -u
d=2026-01-07; G=/data/tk065/g3b; R=/data/tk065/g3b-root; rm -rf $G $R; mkdir -p $G $R
for x in /data/tk046/jan/home/alpha/epochs/e-jan/study/root-$d/ADAUSDT*; do ln -s $x $R/; done
S=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-$d
ln -s $S/session.json $R/; { head -1 $S/instruments.csv; grep '^ADAUSDT,' $S/instruments.csv; } > $R/instruments.csv
ls $R > $G/root.ls
mk() { # <имя> <набор r2|r2t> <клетка-подстрока>
  grep -F -- "$3" /data/tk065/days/$2-$d.cells > $G/$1.cells
  line=$(grep -n 'bounce-grid' /data/tk065/days/$2-$d.sh | head -1 | cut -d: -f1)
  { echo "set -e"; sed -n "${line}p" /data/tk065/days/$2-$d.sh \
    | sed -e "s#--root study/root-$d#--root $R#" -e "s# --set g87-fresh:[^ ]*##" -e "s#--cells [^ ]*#--cells $G/$1.cells#" -e "s#--out-dir [^ ]*#--out-dir $G/$1.out#" -e "s#> [^ ]*cellstmp[^ ]*log 2>&1#> $G/$1.trace 2>\&1#" -e 's#^bin/alpha-tk044k1-new#ALPHA_R2_TRACE=1 bin/alpha-tk044k1-new#'; } > $G/$1.day.sh
}
mk pyre3u1 r2 "-pyre3u1 t-bid"
mk conv0a20 r2 "-conv0a20 t-bid"
mk tsl10t2 r2t "-tsl10t2 t-bid"
for n in pyre3u1 conv0a20 tsl10t2; do
  case $n in tsl10t2) mon=r2t;; *) mon=r2;; esac
  cp $G/$n.day.sh /data/tk065/days/g3b$n-$d.sh
  SCR=g3b$n bash /data/tk065/probe.sh g3b-$n r2m-trace jan $d
  mv /data/tk065/g3b-$n/run.err $G/$n.run.err 2>/dev/null
done
echo ok > /data/tk065/g3b.done
