#!/usr/bin/env bash
# П-42 §13.27 (TK-610): запуск движка на окне TK-599 под systemd-run (Nice=19, CPUWeight=1, idle IO) и ВОЗВРАТ СРАЗУ; ожидание — `bash tools/srv-wait.sh file:<out>/<NAME>/DONE <минут>` фоном.
#   bash tools/p42/tk610_run.sh <NAME> <окно wa|wb|wc> <файл вариантов> <потоков> [ftab-csv на srv] [ждать-md5-движка,…]
# NAME → /data/p42-tk610/out/<NAME>/eng/<id варианта>/{long,short}-{trades.csv,detail.jsonl,sigsx.csv,levels.csv,daily.csv,coinday.csv,entryrej.csv}, DONE — код возврата.
# Деревья окон TK-599 (`/data/p42-tk599/<окно>/{cache,aggtrades}`) — только чтение. Движок — `/data/p42-tk599/bin/alpha-master`; md5 должен быть в списке допустимых
# (по умолчанию a03c91df5a70c8eb32cbb7122943b8f9; после ворот TK-607 — бинарь из `tk599-frozen.json`, список через запятую шестым аргументом).
# Дампы `P42_DETAIL=1 P42_SIGSX=14:73 P42_LEVELS=1` (сигнальные сутки сетки 14…73, как у TK-599). Тяжёлое — ≤ 2 потоков, пока идут TK-599 / 607 / 609 (§13.27 п. 10).
set -euo pipefail
NAME="${1:?имя прогона}"; WIN="${2:?окно wa|wb|wc}"; VF="${3:?файл вариантов}"; TH="${4:?потоков}"; FT="${5:-}"; MD5S="${6:-a03c91df5a70c8eb32cbb7122943b8f9}"
ROOT=/data/p42-tk599; W=/data/p42-tk610; BIN=$ROOT/bin/alpha-master; SYMS=/data/p42-t566/symbols.txt
case "$WIN" in wa) T0=2026-06-17 ;; wb) T0=2026-07-25 ;; wc) T0=2026-09-01 ;; *) echo "окно: wa|wb|wc" >&2; exit 2 ;; esac
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=40)
H=root@5.231.25.224
[ -s "$VF" ] || { echo "нет файла вариантов: $VF" >&2; exit 2; }
ssh "${K[@]}" "$H" "mkdir -p $W/out $W/var && cat > $W/var/$NAME.txt" < "$VF"
FTARG=""; [ -n "$FT" ] && FTARG="--ftab $FT"
ssh "${K[@]}" "$H" "cat > $W/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $W
O=$W/out/$NAME
rm -rf \$O; mkdir -p \$O
md5=\$(md5sum $BIN | cut -d' ' -f1); echo "engine \$md5" > \$O/manifest-engine.txt
case ",$MD5S," in *",\$md5,"*) ;; *) echo 9 > \$O/DONE; echo "md5 движка не из допустимых: \$md5" > \$O/FAIL; exit 9 ;; esac
sha256sum $SYMS > \$O/manifest-symbols.txt
cp $W/var/$NAME.txt \$O/variants.txt
env P42_DETAIL=1 P42_SIGSX=14:73 P42_LEVELS=1 P42_T0=$T0 $BIN breakout tick --cache $ROOT/$WIN/cache --trades $ROOT/$WIN/aggtrades --variants $W/var/$NAME.txt --symbols "\$(cat $SYMS)" --out \$O/eng --threads $TH $FTARG > \$O/engine.log 2>&1
rc=\$?
echo \$rc > \$O/DONE
[ \$rc -eq 0 ] || echo "FAIL $NAME rc=\$rc" > \$O/FAIL
EOS
UNIT="tk610-$NAME"
ssh "${K[@]}" "$H" "systemctl reset-failed $UNIT 2>/dev/null; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $W/run-$NAME.sh >/dev/null 2>&1 && echo started $UNIT"
