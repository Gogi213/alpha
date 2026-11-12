#!/usr/bin/env bash
# TK-499 (В-103): раннер сборки на сервере 5.231.25.224 (вместо calc-build-run.sh). Ставится как /opt/rbuild/run-srv.sh;
# зовёт его tools/vps-check.sh по ssh.
#   run-srv.sh <архив исходников> <файл команды>
# Одна сборка за раз (замок build.lock), общий тёплый target-srv. Не мешает счёту G0 П-40 (юниты p40-*):
# пока они живы — 2 ядра (-j и CPUQuota), nice 19, диск best-effort низший, память ≤ 16 ГБ; иначе SRV_CORES (6 из 8).
# Очередь (проверки ошибок Судьи гонят E-010/E-011 параллельно из нескольких сессий — сборка крейта ≈ 6 мин):
#  - сирота: клиент (ssh) умер, пока запрос ждал замка, — выходим, не собирая впустую (rc 74);
#  - одно содержимое: запросы с тем же деревом и той же командой, пришедшие до начала чужой сборки, берут её результат
#    (ключ — хеш содержимого файлов без mtime + файл команды); результат собранный ДО прихода запроса не берётся —
#    проверки E-011 («пересборка не зависит от прежней сборки») подряд идущие шаги пересобирают, а не читают кэш.
# RBUILD_DIR — каталог раннера (по умолчанию /opt/rbuild; другой — только для проверки самого раннера).
set -u
ARC="$1"; CMDF="$2"
B="${RBUILD_DIR:-/opt/rbuild}"
export RUSTUP_HOME=$B/rustup CARGO_HOME=$B/cargo PATH=$B/cargo/bin:$PATH
P0=$PPID
T0=$(date +%s.%N)
gone() { [ "$(ps -o ppid= -p $$ | tr -d ' ')" != "$P0" ]; }   # клиентская сессия sshd закрылась — нас усыновил init
PRE=$B/pre-$$
cleanup() { rm -rf "$PRE"; rm -f "$ARC" "$CMDF"; }
trap cleanup EXIT
rm -rf "$PRE" && mkdir -p "$PRE" && tar --warning=no-timestamp -xzf "$ARC" -C "$PRE" || exit 70
KEY=$( { (cd "$PRE" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum); cat "$CMDF"; } | sha256sum | cut -c1-16)
mkdir -p "$B/res"
# замок содержимого: ждём чужую сборку того же дерева; затем общий замок (порядок всегда ключ -> общий, взаимной блокировки нет)
exec 8>"$B/res/$KEY.lock"
W=0; until flock -w 10 8; do gone && { echo "rbuild: клиент ушёл — запрос $KEY снят"; exit 74; }; W=$((W + 10)); [ $W -ge 7200 ] && { echo "rbuild: замок содержимого занят > 2 ч"; exit 75; }; done
if [ -e "$B/res/$KEY.rc" ] && awk -v s="$(cat "$B/res/$KEY.start")" -v t="$T0" 'BEGIN{exit !(s >= t)}'; then
  echo "rbuild: результат сборки того же содержимого ($KEY), начатой после прихода запроса"
  cat "$B/res/$KEY.out"; exit "$(cat "$B/res/$KEY.rc")"
fi
export SLOT_TGT=$B/target-srv
exec 9>"$B/build.lock"
W=0; until flock -w 10 9; do gone && { echo "rbuild: клиент ушёл — запрос $KEY снят"; exit 74; }; W=$((W + 10)); [ $W -ge 7200 ] && { echo "rbuild: замок $B/build.lock занят > 2 ч"; exit 75; }; done
gone && { echo "rbuild: клиент ушёл — запрос $KEY снят"; exit 74; }
# ядра — после замка (ожидание до 2 ч): p40-* могли стартовать, пока запрос стоял в очереди
if systemctl list-units 'p40-*' --state=active --no-legend 2>/dev/null | grep -q .; then JOBS=2; else JOBS=${SRV_CORES:-6}; fi
export JOBS
FREE=$(df --output=avail -BG "$B" | tail -1 | tr -dc 0-9)
[ "$FREE" -lt 25 ] && { echo "rbuild: свободно ${FREE}G < 25G — target сброшен"; rm -rf "$SLOT_TGT" "$SLOT_TGT-dbg" "$SLOT_TGT-lint"; }
SRC=$B/src-srv
rm -rf "$SRC" && mv "$PRE" "$SRC" && cd "$SRC" || exit 70
# E-011: tar хранит mtime файлов, cargo считает крейт свежим, если исходники не новее прежней сборки в общем target, —
# результат должен зависеть только от содержимого дерева: исходникам своего крейта mtime = сейчас
find src -name '*.rs' -exec touch {} + ; for f in build.rs Cargo.toml; do [ -e "$f" ] && touch "$f"; done   # touch без проверки создал бы пустой build.rs
echo "rbuild: ядер $JOBS, свободно ${FREE}G, target $(du -sm "$SLOT_TGT" 2>/dev/null | cut -f1) МБ"
OUT=$B/out-srv.log
START=$(date +%s.%N)
systemd-run --scope -q -p CPUQuota=$((JOBS * 100))% -p MemoryMax=16G nice -n 19 ionice -c 2 -n 7 bash "$CMDF" >"$OUT" 2>&1; RC=$?
if [ $RC -eq 0 ]; then tail -60 "$OUT"; else grep -A2 -e '^error' -e '^warning' -e FAILED -e panicked "$OUT" | head -150; echo "--- хвост"; tail -25 "$OUT"; fi > "$B/res/$KEY.out.new"
mv "$B/res/$KEY.out.new" "$B/res/$KEY.out"; echo "$START" > "$B/res/$KEY.start"; echo "$RC" > "$B/res/$KEY.rc.new"; mv "$B/res/$KEY.rc.new" "$B/res/$KEY.rc"
cat "$B/res/$KEY.out"
find "$B/res" -type f -mmin +1440 -delete 2>/dev/null
exit $RC
