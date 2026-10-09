set -u
export REG_DIR=/tmp/regtest118; rm -rf $REG_DIR; mkdir -p $REG_DIR; cp /data/registry/snap.py /tmp/ 2>/dev/null
cd /data/registry
G="python3 /data/registry/guard.py"
OLD=/data/tk064/bin/alpha-tk064-r1; NEW=/opt/alpha-compute/bin/alpha-b26tk115r2
d=2026-02-01; V=/data/tk044/final3/verdict.csv
OUT=/data/tk064/pool/m-feb/signals/$d
echo "gate: старые 72 колонки signals/rounds побайтно = TK-064 (TK-115 лог, проба режима c)" > /tmp/gate118.txt
cmdb() { echo "$1 lob bounce-grid --verdict-csv $V --root study/root-$d --touches-from study/approaches/D20 --signal approach --queue-model prob:3 --entry-form ladder3x0..0.0409sw2 --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --r1-cols --out-dir $2"; }
IN=/data/tk046/feb/home/alpha/epochs/e-feb/study/root-$d
echo "== adopt (старый бинарник, чужое рабочее дерево /w1)"; $G adopt b1grid --out $OUT --in $IN --norm /w1=W --gate /tmp/gate118.txt -- $(cmdb $OLD /w1/b5/out-$d)
echo "== step новый БЕЗ equiv (ждём СЧИТАЕМ)"; $G step b1grid --out $OUT --in $IN --norm /w2=W --dry -- $(cmdb $NEW /w2/b5/out-$d)
echo "== equiv"; $G equiv --new $NEW --old $OLD --scope b1grid --gate /tmp/gate118.txt
echo "== step новый после equiv (ждём ГОТОВО)"; $G step b1grid --out $OUT --in $IN --norm /w2=W --dry -- $(cmdb $NEW /w2/b5/out-$d)
