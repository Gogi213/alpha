#!/bin/bash
# TK-463: проверка приёмки ядра tk463-sim — читает логи гейтов на calc по ssh, rc != 0 при несовпадении любого счётчика.
# Использование: bash tools/compute/tk463-check.sh   (ключи/хосты — ~/.ssh/config, алиас calc)
set -u
D=/data/tk0463
out=$(ssh -o BatchMode=yes -o ConnectTimeout=30 calc "
echo md5 \$(md5sum $D/bin/tk463-sim | cut -c1-8)
echo d2a \$(grep -c 'cmp: IDENTICAL 252 из 252' $D/d2a_full/full.log)
echo gate_done \$(grep -c 'jobs 21 ok 42' $D/out/GATE.DONE)
echo gate_ident \$(grep -c IDENTICAL $D/out/gate.log)
echo gate_bad \$(grep -c -v IDENTICAL $D/out/gate.log)
echo twt1440ctrl \$(grep -c '^ctrl TWTUSDT 1440 .*IDENTICAL' $D/out/gate.log)
echo d2b_bytes \$(grep -c BYTES_EQ $D/d2b/gate2.log)
echo d2b_ident \$(grep -c IDENTICAL $D/d2b/gate2.log)
echo synth \$(grep -c IDENTICAL $D/synth/gate.log)
") || { echo "FAIL: ssh calc недоступен" >&2; exit 2; }
echo "$out"
rc=0
chk() { [ "$(echo "$out" | awk -v k="$1" '$1==k{print $2}')" = "$2" ] || { echo "FAIL: $1 != $2" >&2; rc=1; }; }
chk md5 fa014fb0
chk d2a 1
chk gate_done 1
chk gate_ident 42
chk gate_bad 0
chk twt1440ctrl 1
chk d2b_bytes 9
chk d2b_ident 9
chk synth 6
[ $rc = 0 ] && echo "OK: все счётчики сошлись"
exit $rc
