#!/usr/bin/env bash
# TK-499 (В-103): раннер сборки на сервере 5.231.25.224 (вместо calc-build-run.sh). Клиенты (tools/vps-check.sh,
# tools/p42/build-srv.sh) выкладывают его версией /opt/rbuild/run-srv.<md5>.sh (TK-614: общий run-srv.sh не перезаписывается)
# и зовут по ssh:
#   [RBUILD_ID=<id>] [RBUILD_PRIO=0|1|2] run-srv.<md5>.sh <архив исходников> <файл команды>
# Одна сборка за раз (замок build.lock), общий тёплый target-srv. Не мешает счёту G0 П-40 (юниты p40-*):
# пока они живы — 2 ядра (-j и CPUQuota), nice 19, диск best-effort низший, память ≤ 16 ГБ; иначе SRV_CORES (6 из 8).
# Очередь (TK-614, аудит TK-612 §4):
#  - порядок: приоритет (0 — бинарь критического пути, 1 — ворота/проверки, 2 — `all`), при равном — по приходу запроса
#    (файл $B/queue/<приоритет>-<нс прихода>-<id>); замок берёт только голова очереди;
#  - сборка идёт в именованном scope `rbuild-<id>`; `bash $B/cancel.sh <id>` снимает запрос из очереди или останавливает
#    только этот scope (rc 73, результат не сохраняется, `install` не выполняется);
#  - в файл команды экспортируется RBUILD_T0 — время прихода запроса (tools/rbuild-install.sh: старый запрос не затирает новый);
#  - сирота: клиент (ssh) умер, пока запрос ждал в очереди, — выходим, не собирая впустую (rc 74);
#  - одно содержимое: запросы с тем же деревом и той же командой, пришедшие до начала чужой сборки, берут её результат
#    (ключ — хеш содержимого файлов без mtime + файл команды); результат собранный ДО прихода запроса не берётся —
#    проверки E-011 («пересборка не зависит от прежней сборки») подряд идущие шаги пересобирают, а не читают кэш.
# Совместимость: раннеры старой версии берут build.lock напрямую, без очереди — очередь их не видит.
# RBUILD_DIR — каталог раннера (по умолчанию /opt/rbuild; другой — только для проверки самого раннера).
set -u
ARC="$1"; CMDF="$2"
B="${RBUILD_DIR:-/opt/rbuild}"
export RUSTUP_HOME=$B/rustup CARGO_HOME=$B/cargo PATH=$B/cargo/bin:$PATH LC_ALL=C
P0=$PPID
T0=$(date +%s.%N); TNS=$(date +%s%N)
Q=$B/queue; RUN=$B/running; CAN=$B/cancel
mkdir -p "$Q" "$RUN" "$CAN" "$B/res"
ID=$(printf '%s' "${RBUILD_ID:-$$}" | tr -c 'A-Za-z0-9_.' '_')
PRIO="${RBUILD_PRIO:-2}"; case "$PRIO" in [0-9]) ;; *) PRIO=2 ;; esac
alive() { [ -n "$1" ] && [ -d "/proc/$1" ]; }
# id занят живым запросом — добавляем свой pid (отмена адресует id)
for f in "$Q"/?-*-"$ID" "$RUN/$ID"; do [ -e "$f" ] && alive "$(cat "$f" 2>/dev/null)" && { ID=$ID.$$; break; }; done
ME=$PRIO-$TNS-$ID
gone() { [ "$(ps -o ppid= -p $$ | tr -d ' ')" != "$P0" ]; }   # клиентская сессия sshd закрылась — нас усыновил init
PRE=$B/pre-$$
cleanup() { rm -rf "$PRE"; rm -f "$ARC" "$CMDF" "$Q/$ME" "$RUN/$ID" "$CAN/$ID" "$Q/.$ME.tmp"; }
trap cleanup EXIT
trap 'exit 143' TERM HUP INT
echo $$ > "$Q/.$ME.tmp" && mv "$Q/.$ME.tmp" "$Q/$ME"
echo "rbuild: id=$ID prio=$PRIO, отмена: bash $B/cancel.sh $ID"
rm -rf "$PRE" && mkdir -p "$PRE" && tar --warning=no-timestamp -xzf "$ARC" -C "$PRE" || exit 70
KEY=$( { (cd "$PRE" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum); cat "$CMDF"; } | sha256sum | cut -c1-16)
# голова очереди: первая живая запись — моя; мёртвые записи (раннер убит) подчищаем
qhead() {
  local f
  for f in $(ls "$Q"); do
    [ "$f" = "$ME" ] && return 0
    alive "$(cat "$Q/$f" 2>/dev/null)" && return 1
    rm -f "$Q/$f"
  done
  return 1
}
# чужая сборка того же содержимого, начатая после прихода запроса, — берём её результат (замок содержимого — только на чтение/запись, не на ожидание)
reuse() {
  [ -e "$B/res/$KEY.rc" ] || return 1
  exec 8>"$B/res/$KEY.lock"; flock -n 8 || return 1
  if [ -e "$B/res/$KEY.rc" ] && awk -v s="$(cat "$B/res/$KEY.start")" -v t="$T0" 'BEGIN{exit !(s >= t)}'; then
    echo "rbuild: результат сборки того же содержимого ($KEY), начатой после прихода запроса"
    cat "$B/res/$KEY.out"; RC=$(cat "$B/res/$KEY.rc"); exec 8>&-; exit "$RC"
  fi
  exec 8>&-; return 1
}
export SLOT_TGT=$B/target-srv
exec 9>"$B/build.lock"
W=0
while :; do
  reuse
  qhead && flock -n 9 && break
  [ -e "$Q/$ME" ] || { echo "rbuild: запрос $ID снят с очереди (cancel)"; exit 73; }
  gone && { echo "rbuild: клиент ушёл — запрос $ID снят"; exit 74; }
  sleep 1; W=$((W + 1)); [ $W -ge 7200 ] && { echo "rbuild: очередь/замок $B/build.lock заняты > 2 ч"; exit 75; }
done
echo $$ > "$RUN/$ID"; rm -f "$Q/$ME"
[ -e "$CAN/$ID" ] && { echo "rbuild: запрос $ID отменён"; exit 73; }
# ядра — после замка (ожидание до 2 ч): p40-* могли стартовать, пока запрос стоял в очереди
if systemctl list-units 'p40-*' --state=active --no-legend 2>/dev/null | grep -q .; then JOBS=2; else JOBS=${SRV_CORES:-6}; fi
export JOBS RBUILD_T0=$T0
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
systemd-run --scope -q --unit="rbuild-$ID" -p CPUQuota=$((JOBS * 100))% -p MemoryMax=16G nice -n 19 ionice -c 2 -n 7 bash "$CMDF" >"$OUT" 2>&1; RC=$?
[ $RC -ne 0 ] && [ -e "$CAN/$ID" ] && { echo "rbuild: запрос $ID отменён во время сборки (rc $RC), результат не сохранён"; tail -5 "$OUT"; exit 73; }
if [ $RC -eq 0 ]; then tail -60 "$OUT"; else grep -A2 -e '^error' -e '^warning' -e FAILED -e panicked "$OUT" | head -150; echo "--- хвост"; tail -25 "$OUT"; fi > "$B/res/$KEY.out.new"
# результат пишем под замком содержимого с таймаутом: раннер старой версии мог занять его на всё ожидание, ждать его нельзя (взаимная блокировка)
if exec 8>"$B/res/$KEY.lock" && flock -w 20 8; then
  mv "$B/res/$KEY.out.new" "$B/res/$KEY.out"; echo "$START" > "$B/res/$KEY.start"; echo "$RC" > "$B/res/$KEY.rc.new"; mv "$B/res/$KEY.rc.new" "$B/res/$KEY.rc"
  exec 8>&-
else
  mv "$B/res/$KEY.out.new" "$B/res/$KEY.out.own"
fi
cat "$B/res/$KEY.out" 2>/dev/null || cat "$B/res/$KEY.out.own"
find "$B/res" -type f -mmin +1440 -delete 2>/dev/null; find "$CAN" -type f -mmin +1440 -delete 2>/dev/null
exit $RC
