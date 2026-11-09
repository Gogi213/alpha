#!/bin/bash
# TK-500: гейт приёмки на 5.231.25.224 одной командой (код ≠ 0 — отказ): ssh srv 'bash /root/p40core/srv-gate.sh old'
# 1) cargo test --release (все тесты ядра) + сборка + 5 таблиц DOGE 17.09 байт в байт с базой /tmp/<$1>.csv* (srv-test.sh);
# 2) сверка с эталоном TK-501 (/root/p40ref/out/ep): заявки/выходы, МПУ-путь, признаки, aux — без расхождений.
#    d_in и plevel: 41/50 — известный допуск отчёта §0 (≤ 3·10⁻⁴ bps в «карманах» уровня), остальное 50/50.
# Базу /tmp/<$1>.csv* снимают прежним бинарём ДО правки; нагрузка: nice 19, 1 ядро.
P=${P40_DIR:-/root/p40core}
NEW=${P40_NEW:-/tmp/new.csv}
export P40_DIR=$P P40_NEW=$NEW
cd $P || exit 1
EP=/root/p40ref/out/ep
fail=0
out=$(TAIL=40 nice -n 19 bash $P/srv-test.sh "${1:-old}" 2>&1)
echo "$out" | tail -12
n_eq=$(echo "$out" | grep -c "байт в байт")
n_ok=$(echo "$out" | grep -c "test result: ok")
if [ "$n_eq" != 5 ] || [ "$n_ok" -lt 1 ] || echo "$out" | grep -q "FAILED\|error\["; then echo "ГЕЙТ: тесты/байт в байт — отказ ($n_eq/5, ok=$n_ok)"; fail=1; fi
# TK-510: тесты aux_feats (час без mark/OI) и бюджета shm orch (без pytest: python3 <файл>)
for t in test_aux_feats.py test_orch_shm.py; do
  python3 $P/$t > /tmp/gate_$t.log 2>&1 || { tail -5 /tmp/gate_$t.log; echo "ГЕЙТ: $t — отказ"; fail=1; }
done
cd $EP || exit 1
m=$(nice -n 19 python3 $P/cmp_maker.py $NEW.fills.csv maker_5.04.csv maker_12.6.csv $NEW.exits.csv 2>&1 | tail -2)
echo "$m"
[ "$(echo "$m" | grep -c "расхождений 0$")" = 2 ] || { echo "ГЕЙТ: cmp_maker — отказ"; fail=1; }
f=$(nice -n 19 python3 $P/cmp_feats.py $NEW.features.csv features_5.04.csv features_12.6.csv 2>&1 | tail -1)
echo "$f"
echo "$f" | python3 -c "
import ast, sys
d = ast.literal_eval(sys.stdin.read())
bad = [k for k, v in d.items() if (v != '50/50' if k not in ('d_in', 'plevel') else int(v.split('/')[0]) < 41)]
sys.exit(1 if bad or len(d) != 9 else 0)" || { echo "ГЕЙТ: cmp_feats — отказ"; fail=1; }
for c in "cmp_aux.py $NEW.aux.csv features_5.04.csv features_12.6.csv" "cmp_path.py $NEW.path.csv . 5.04 12.6"; do
  r=$(nice -n 19 python3 $P/$c 2>&1 | tail -1)
  echo "$r"
  echo "$r" | python3 -c "
import ast, sys
d = ast.literal_eval(sys.stdin.read())
sys.exit(0 if d and all(v == '50/50' for v in d.values()) else 1)" || { echo "ГЕЙТ: ${c%% *} — отказ"; fail=1; }
done
[ $fail = 0 ] && echo "ГЕЙТ TK-500: ок"
exit $fail
