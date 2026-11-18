#!/usr/bin/env bash
# П-42 §13.23 (TK-598): запуск движка v21 для окна на srv под systemd-run (6 потоков, Nice=19, CPUQuota=600%, CPUWeight=1, idle IO) и ВОЗВРАТ СРАЗУ
# (ожидание — bash tools/srv-wait.sh file:/data/p42-tk598/out/<NAME>/DONE <минут>, фоном; п. 33 приказов).
#   bash tools/p42/tk598_run.sh <NAME> <окно: F|jul|aug|hold> <файл вариантов (id ключи…)>
# Окна (протокол §13.23 п. 3): F — сутки 14…73 от 01.09, кэш cache80; jul — P42_T0=2026-06-17, строки 14…44 = 01–31.07 (вход TK-596, только чтение);
# aug — P42_T0=2026-07-18, 14…44 = 01–31.08 (вход TK-596, 566 монет); hold — P42_T0=2026-10-31, 14…16 = 14–16.11 (cache-hold, ленты TK-592).
# Включает дампы `P42_DETAIL=1 P42_SIGSX=<дни> P42_LEVELS=1` и sha256-манифесты входов (до запуска движка: пишутся в out/<NAME>/manifest-*.txt).
# Результат: /data/p42-tk598/out/<NAME>/<id варианта>/{long,short}-{trades.csv,detail.jsonl,sigsx.csv,levels.csv,daily.csv}; DONE — код возврата.
set -euo pipefail
NAME="${1:?имя прогона}"; WIN="${2:?окно F|jul|aug|hold}"; VF="${3:?файл вариантов}"
BIN=/data/p42-t566/bin/breakout-v21; SYMS=/data/p42-t566/symbols.txt; ROOT=/data/p42-tk598; TH=6
case "$WIN" in
  F)    T0=2026-09-01; CACHE=/data/p42-t566/cache80;            TR=/data/aggtrades/binance;          DAYS=14:73 ;;
  jul)  T0=2026-06-17; CACHE=/data/p42-tk596/jul/cache;         TR=/data/p42-tk596/jul/aggtrades;    DAYS=14:44 ;;
  aug)  T0=2026-07-18; CACHE=/data/p42-tk596/aug/cache;         TR=/data/p42-tk596/aug/aggtrades;    DAYS=14:44 ;;   # вход TK-596 (566 монет), решение координатора 18.11
  hold) T0=2026-10-31; CACHE=/data/p42-retitr/cache-hold;       TR=/data/tk592/aggtrades;            DAYS=14:16 ;;
  *) echo "окно: F|jul|aug|hold" >&2; exit 2 ;;
esac
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
[ -s "$VF" ] || { echo "нет файла вариантов: $VF" >&2; exit 2; }
ssh "${K[@]}" "$H" "mkdir -p $ROOT/out $ROOT/var && cat > $ROOT/var/$NAME.txt" < "$VF"
ssh "${K[@]}" "$H" "cat > $ROOT/run-$NAME.sh" <<EOS
#!/usr/bin/env bash
cd $ROOT
O=$ROOT/out/$NAME
rm -rf \$O; mkdir -p \$O
md5=\$(md5sum $BIN | cut -d' ' -f1); echo "engine \$md5" > \$O/manifest-engine.txt
[ "\$md5" = e7891721ee839182bdf66bc37ccf0f6d ] || { echo 9 > \$O/DONE; echo "md5 движка не совпал: \$md5" > \$O/FAIL; exit 9; }
sha256sum $SYMS > \$O/manifest-symbols.txt
(cd $CACHE && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > \$O/manifest-cache.txt
(cd $TR && find -L . -type f -printf '%p %s\n' | LC_ALL=C sort) > \$O/manifest-trades.txt
sha256sum \$O/manifest-cache.txt \$O/manifest-trades.txt > \$O/manifest-sha.txt
cp $ROOT/var/$NAME.txt \$O/variants.txt
env P42_DETAIL=1 P42_SIGSX=$DAYS P42_LEVELS=1 P42_T0=$T0 $BIN breakout tick --cache $CACHE --trades $TR --variants $ROOT/var/$NAME.txt --symbols "\$(cat $SYMS)" --out \$O/eng --threads $TH > \$O/engine.log 2>&1
rc=\$?
echo \$rc > \$O/DONE
[ \$rc -eq 0 ] || echo "FAIL $NAME rc=\$rc" > \$O/FAIL
EOS
UNIT="tk598-$NAME"
ssh "${K[@]}" "$H" "systemctl reset-failed $UNIT 2>/dev/null; systemd-run --unit=$UNIT -p Nice=19 -p CPUQuota=$((TH*100))% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash $ROOT/run-$NAME.sh >/dev/null 2>&1 && echo started $UNIT"
