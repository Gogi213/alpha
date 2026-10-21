#!/usr/bin/env bash
# TK-279 П-17: счёт Г-81 (72 клетки на сутки: L-<мес>.cells, порог ворот cxl<W>q<порог> по месяцу; tape-пачка --tape-log 300), боевой PGO (В-21).
#   План /data/tk0279/p17plan-g81, выход /data/tk0279/p17g81/out; env NX (=ядра, умолч. 12), GT=1.
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов (нет /data/tk0279/pgo.ok) - пропуск, подать заново"; exit 75; }
export REASON="TK-279 П-17 счёт Г-81 (ворота отмен, пороги по месяцу), pgo" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=${NX:-12} GT=${GT:-1}
export PLANSRC=/data/tk0279/p17plan-g81; bash /data/tk0279/p17-count.sh /data/tk0279/p17g81 /data/tk0279/days9.txt
rc=$?
n=$(cat /data/tk0279/p17g81/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
