#!/usr/bin/env bash
# Проверка sb-move.sh на копии (TK-020, гейт Судьи 02.10): сценарии плана + п. 3. На коллекторе; ящик — только
# каталог alpha/tk020-test (маленькие синтетические файлы), боевые данные не трогаются.
#   bash sb-move-test.sh <каталог с sb-move.sh и sb-push-day.sh> <рабочий каталог теста>
set -uo pipefail
T=${1:?каталог скриптов}; W=${2:?рабочий каталог}
[ "$(id -u)" -eq 0 ] || { echo "запускать от root (sudo): боевой юнит — root, коллектор — root"; exit 2; }
export ALPHA_TEST=1 ALPHA_BASE=$W SB_REMOTE_PREFIX=alpha/tk020-test/$(basename "$W") ALPHA_TODAY=2026-02-01 DISK_USE_FAKE=10 BOX_USE_FAKE=10 SB_KEYDIR=${SB_KEYDIR:-/home/ubuntu/.ssh}
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $SB_KEYDIR/id_storagebox -o UserKnownHostsFile=$SB_KEYDIR/known_hosts -o BatchMode=yes"
$SSHC $SBH mkdir -p alpha/tk020-test/$(basename "$W")/root alpha/tk020-test/$(basename "$W")/deep
mkdir -p "$W/root/deep" "$W/verify" "$W/sync/sb"; printf 'ts,event\n' > "$W/root/gaps.csv"
ok=0; fail=0
check() { if eval "$2"; then echo "PASS $1"; ok=$((ok + 1)); else echo "FAIL $1   [$2]"; fail=$((fail + 1)); fi; }
mk() { head -c "${3:-40000}" /dev/urandom > "$1/$2"; touch -d "2026-01-01 12:00:00" "$1/$2"; }   # mk <каталог> <имя> [байт]
lastmove() { grep -E "MOVED $1|$1" "$W/sync/sb/move.log" | tail -1; }
run() { bash "$T/sb-move.sh" "$@" > /dev/null 2>&1; }

# S1: всё сверено, есть сверка суток → удалено, ссылки verify удалены
D=2026-01-02
for c in AAA BBB CCC; do mk "$W/root" "${c}USDT-$D.binlog"; done; for c in AAA BBB; do mk "$W/root/deep" "${c}USDT-$D.binlog"; done
mkdir -p "$W/verify/$D"; for c in AAA BBB CCC; do ln "$W/root/${c}USDT-$D.binlog" "$W/verify/$D/${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"
run "$D"
check "S1 root и deep удалены" '[ -z "$(ls $W/root/*-$D*.binlog $W/root/deep/*-$D*.binlog 2>/dev/null)" ]'
check "S1 ссылки verify удалены" '[ -z "$(ls $W/verify/$D 2>/dev/null)" ]'
check "S1 строка MOVED 5 файлов" 'grep -q "MOVED $D: удалено файлов 5" $W/sync/sb/move.log'
check "S1 на ящике 5 файлов" '[ "$($SSHC $SBH ls alpha/tk020-test/$(basename $W)/root | grep -c -- "-$D")" -ge 3 ] && [ "$($SSHC $SBH ls alpha/tk020-test/$(basename $W)/deep | grep -c -- "-$D")" -ge 2 ]'
check "S1 нет ALERT" '[ ! -e $W/sync/sb/ALERT-$D ]'

# S2: на ящике файл с тем же размером и mtime, другой байт-в-байт → MISMATCH, ничего не удалено, ALERT
D=2026-01-03
for c in AAA BBB; do mk "$W/root" "${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"
head -c 40000 /dev/urandom > "$W/alt.bin"; touch -d "2026-01-01 12:00:00" "$W/alt.bin"
rsync -t -e "$SSHC" "$W/alt.bin" "$SBH:$SB_REMOTE_PREFIX/root/BBBUSDT-$D.binlog"
run "$D"
check "S2 файлы целы" '[ "$(ls $W/root/*-$D.binlog | wc -l)" -eq 2 ]'
check "S2 ALERT" '[ -s $W/sync/sb/ALERT-$D ]'

# S3: один файл нечитаем (rsync не донёс) → ничего не удалено, ALERT
D=2026-01-04
for c in AAA BBB CCC; do mk "$W/root" "${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"; chmod 000 "$W/root/CCCUSDT-$D.binlog"
run "$D"
check "S3 все три целы" '[ "$(ls $W/root/*-$D.binlog | wc -l)" -eq 3 ]'
check "S3 ALERT" '[ -s $W/sync/sb/ALERT-$D ]'
chmod 600 "$W/root/CCCUSDT-$D.binlog"

# S4: файл суток, которого нет в манифесте (появился после) → цел, остальное перенесено
D=2026-01-05
for c in AAA BBB; do mk "$W/root" "${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"
export ALPHA_TEST_HOOK="sleep 1; head -c 1000 /dev/urandom > $W/root/LATEUSDT-$D.binlog"
run "$D"; unset ALPHA_TEST_HOOK
check "S4 поздний файл цел" '[ -f $W/root/LATEUSDT-$D.binlog ]'
check "S4 манифестные удалены" '[ ! -e $W/root/AAAUSDT-$D.binlog ] && [ ! -e $W/root/BBBUSDT-$D.binlog ]'
run "$D"   # следующий прогон догоняет поздний
check "S4 догон: поздний ушёл" '[ ! -e $W/root/LATEUSDT-$D.binlog ]'

# S5: файл открыт процессом → цел, ALERT
D=2026-01-06
for c in AAA BBB; do mk "$W/root" "${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"
( exec 8< "$W/root/AAAUSDT-$D.binlog"; exec sleep 25 ) & HP=$!; sleep 1   # держит отдельный процесс root, как коллектор
check "S5 fuser под ubuntu файл root-процесса не видит (почему нужен root)" '! runuser -u ubuntu -- fuser -s "$W/root/AAAUSDT-$D.binlog" 2>/dev/null'
check "S5 fuser под root видит" 'fuser -s "$W/root/AAAUSDT-$D.binlog" 2>/dev/null'
run "$D"; kill $HP 2>/dev/null; wait $HP 2>/dev/null
check "S5 открытый цел и ALERT" '[ -f $W/root/AAAUSDT-$D.binlog ] && [ -f $W/root/BBBUSDT-$D.binlog ] && [ -s $W/sync/sb/ALERT-$D ]'

# S6: сутки не закрыты
D=2026-02-01; mk "$W/root" "AAAUSDT-$D.binlog"; run "$D"
check "S6 сегодняшние целы" '[ -f $W/root/AAAUSDT-$D.binlog ]'

# S7: нет сверки суток → залито, но не удалено, ALERT
D=2026-01-07; for c in AAA BBB; do mk "$W/root" "${c}USDT-$D.binlog"; done; run "$D"
check "S7 без verify-лога цел" '[ -f $W/root/AAAUSDT-$D.binlog ] && [ -s $W/sync/sb/ALERT-$D ]'

# S8: тревоги заполнения
BOX_USE_FAKE=90 DISK_USE_FAKE=80 run
check "S8 ALERT-box и ALERT-disk" '[ -s $W/sync/sb/ALERT-box ] && [ -s $W/sync/sb/ALERT-disk ]'
run; check "S8 тревоги снимаются" '[ ! -e $W/sync/sb/ALERT-box ] && [ ! -e $W/sync/sb/ALERT-disk ]'

# S9: догон — без аргумента обходит все сутки (S7 получает сверку и уходит)
echo ok > "$W/verify/2026-01-07.log"; run
check "S9 догон без аргумента" '[ ! -e $W/root/AAAUSDT-2026-01-07.binlog ]'
# S10: удаление не прошло (файл неизменяемый, chattr +i) → PARTIAL, не MOVED, ALERT; после снятия — догон
D=2026-01-08; for c in AAA BBB; do mk "$W/root" "${c}USDT-$D.binlog"; done; echo ok > "$W/verify/$D.log"
chattr +i "$W/root/BBBUSDT-$D.binlog"; run "$D"
check "S10 PARTIAL, не MOVED, ALERT, неудалённый цел" 'grep -q "PARTIAL $D" $W/sync/sb/move.log && ! grep -q "MOVED $D" $W/sync/sb/move.log && [ -s $W/sync/sb/ALERT-$D ] && [ -f $W/root/BBBUSDT-$D.binlog ] && [ ! -e $W/root/AAAUSDT-$D.binlog ]'
chattr -i "$W/root/BBBUSDT-$D.binlog"; run "$D"
check "S10 после снятия: MOVED, ALERT снят" 'grep -q "MOVED $D" $W/sync/sb/move.log && [ ! -e $W/sync/sb/ALERT-$D ] && [ ! -e $W/root/BBBUSDT-$D.binlog ]'

# S11: запуск не от root → выход с кодом 3, ничего не тронуто
D=2026-01-09; mk "$W/root" "AAAUSDT-$D.binlog"; echo ok > "$W/verify/$D.log"
runuser -u ubuntu -- bash "$T/sb-move.sh" "$D" > /dev/null 2>&1; rc=$?
check "S11 не от root: код 3, файл цел" '[ $rc -eq 3 ] && [ -f $W/root/AAAUSDT-$D.binlog ]'
echo "ИТОГ: pass $ok, fail $fail"; [ "$fail" -eq 0 ]
