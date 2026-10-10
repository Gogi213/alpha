#!/usr/bin/env bash
# Сборка и проверки рабочего дерева на VPS (13.140.29.171) — вторая дорожка работ, пока на этой машине идёт
# своя сборка (владелец 24.09: «одна сборка здесь, вторая на VPS»; две сборки на одной машине — нельзя).
#   tools/vps-check.sh <рабочее дерево> [test|clippy|fmt|build|all|gate|complexity] [фильтр тестов]
# Переносит отслеживаемые и новые файлы дерева (без target*/data/) архивом с сохранением времени изменения —
# неизменённые файлы cargo не пересобирает; сборка — в отдельном target-wave2, не в каталоге сборок для дека.
# Сборки на VPS идут по очереди: замок /opt/alpha-compute/.build.lock (его же берёт сборка бинарника для дека).
# TK-170 (В-223): хост по умолчанию — сервер счёта (CHECK_HOST=calc): сборка под `alsched submit`, N слотов параллельно
# (раннер tools/calc-build-run.sh = /opt/rbuild/run.sh), у слота постоянный тёплый target; красное — все ^error/^warning.
# CHECK_HOST=vps — запасной прежний путь (очередь под замком VPS).
set -euo pipefail
TREE="${1:?рабочее дерево}"
WHAT="${2:-all}"
FILTER="${3:-}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30)
CHECK_HOST="${CHECK_HOST:-calc}"
case "$CHECK_HOST" in calc) HOST=root@89.163.242.211 ;; vps) HOST=root@13.140.29.171 ;; *) echo "CHECK_HOST: calc|vps"; exit 2 ;; esac
TAG="${VPS_TAG:-}"   # непустой тег — свой каталог исходников и target: параллельные вызовы не затирают друг друга
SRC=/opt/alpha-compute/wave2-src$TAG-$$   # TK-169: свой каталог на вызов — ожидающий замка не теряет исходники из-за чужого rm -rf
# TK-169: общий постоянный target (сборки идут по очереди под замком — гонки нет): зависимости из реестра не
# пересобираются; свои крейты cargo пересобирает по mtime (SRC разных тегов — разные пути). Чистит его sweep.sh run
# при свободном месте < порога (SWEEP_SHARED_FLOOR_GB), иначе живёт
TGT=/opt/alpha-compute/shared-target
ARCR=/opt/alpha-compute/wave2$TAG.tgz
# TK-169: при красном — все строки ^error/^warning (+2 контекста), а не хвост (повторные сборки ради текста); true — grep без совпадений не ломает pipefail
ERRS='L=$(mktemp); tee $L | tail -N; grep -A2 -e ^error -e ^warning $L | head -150; rm -f $L; true'
tl() { echo "| { ${ERRS/N/$1}; }"; }
J=3; TL10="$(tl 10)"; TL15="$(tl 15)"; TL25="$(tl 25)"; TL5="| tail -5"
GREP_T="| grep -E \"^test result|FAILED|panicked|^error|^warning: unused\" | tail -40"
if [ "$CHECK_HOST" = calc ]; then   # фильтры на раннере (при красном — все error/warning), target слота и ядра — из его окружения
  TGT='$SLOT_TGT'; J='$JOBS'; TL10=; TL15=; TL25=; TL5=; GREP_T=
  ARCR=/opt/rbuild/in/wave2$TAG-$$.tgz
fi
TMPD="${TEMP:-/tmp}"; command -v cygpath >/dev/null && TMPD="$(cygpath -u "$TMPD")"
ARC="$TMPD/wave2-$$.tgz"
cd "$TREE"
# КТ-1 (TK-145): защита от россыпи — новые tools/compute/(tk|p)<цифра>*-* предупреждение, корень выше базы — отказ
case "$WHAT" in all|gate) python "$(dirname "$0")/check-sprawl.py" . master || exit 1 ;; esac
# метка задачи в командной строке замка (видна в `ps` на VPS — экран хода работ по ней находит задачу сборки):
# RPV_TICKET (прежнее ALPHA_TICKET) сессии роли (его ставит диспетчер), иначе имя рабочего дерева; на поведение сборки не влияет
LABEL="$(printf '%s' "${RPV_TICKET:-${ALPHA_TICKET:-$(basename "$PWD")}}" | tr -c 'A-Za-z0-9._-' '_')"
# TK-170: живая сборка этой метки на calc (queued/running) — отказ с её id (повтор после смерти сессии не плодит дублей); CALC_FORCE=1 — обход
if [ "$CHECK_HOST" = calc ] && [ -z "${CALC_FORCE:-}" ]; then
  LIVE=$(ssh "${KEY[@]}" "$HOST" "python3 /data/sched/alsched.py ps" 2>/dev/null | grep -E "^[0-9]+ +(queued|running) .* build-$LABEL +" | head -1 | cut -d" " -f1) || true
  [ -n "$LIVE" ] && { echo "build-$LABEL уже идёт: id $LIVE — ждать: host:calc:/data/sched/rc/$LIVE (лог /data/sched/logs/$LIVE.log); CALC_FORCE=1 — подать ещё"; exit 75; }
fi
git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
ssh "${KEY[@]}" "$HOST" "mkdir -p $(dirname "$ARCR")"
scp -q "${KEY[@]}" "$ARC" "$HOST:$ARCR"
rm -f "$ARC"
# cargo-фичи (TK-065: R2 — `VPS_FEATURES=r2`); пусто = боевой набор без фич
FEAT="${VPS_FEATURES:+--features $VPS_FEATURES}"
# fmt + clippy -D warnings + тесты с набором фич $1 (пусто — без фич)
all_cmd() {
  echo "set -o pipefail; cargo fmt --check 2>&1 $TL10 && cargo clippy --release $1 --target-dir $TGT --all-targets -j $J -- -D warnings 2>&1 $TL15 && cargo test --release $1 --target-dir $TGT -j $J 2>&1 $GREP_T"
}
case "$WHAT" in
  test)  CMD="cargo test --release $FEAT --target-dir $TGT -j $J $FILTER 2>&1 $GREP_T" ;;
  clippy) CMD="cargo clippy --release $FEAT --target-dir $TGT --all-targets -j $J -- -D warnings 2>&1 $TL25" ;;
  fmt)    CMD="cargo fmt --check 2>&1 $TL25" ;;
  build)  CMD="cargo build --release $FEAT --target-dir $TGT -j $J 2>&1 $TL5" ;;
  # TK-160: бинарники для гейта прямо на машине сборки, без копирования: без фич и r2 → $BIN_OUT-plain / $BIN_OUT-r2 + md5
  bin)    : "${BIN_OUT:?BIN_OUT — путь на машине сборки, например /data/tk0160/alpha-tk160}"
          B1="cargo build --release --target-dir $TGT -j $J"
          CMD="set -o pipefail; $B1 2>&1 | tail -3 && install -D $TGT/release/alpha $BIN_OUT-plain && $B1 --features r2 2>&1 | tail -3 && install -D $TGT/release/alpha $BIN_OUT-r2 && md5sum $BIN_OUT-plain $BIN_OUT-r2" ;;
  all)    CMD="$(all_cmd "$FEAT")" ;;
  # TK-132 С-02: гейт R2 — второй бинарник (feature r2) гоняет 14+4 R2-теста, которые `all` без фич не видит:
  # `gate` = `all` без фич, затем то же с `--features r2`
  gate)   CMD="$(all_cmd "") && $(all_cmd "--features r2")" ;;
  # TK-132 (А1 §0): настоящая метрика — clippy cognitive_complexity (порог 15) и too_many_lines; код не меняется,
  # clippy.toml пишется только в одноразовую копию дерева на VPS. Вывод: «функция @ файл:строка», по алфавиту
  complexity) CMD="echo cognitive-complexity-threshold = 15 > clippy.toml && cargo clippy --release $FEAT --target-dir $TGT --all-targets -j 3 -- -W clippy::cognitive_complexity -W clippy::too_many_lines 2>&1 | grep -E -A1 \"cognitive complexity of|too many lines\" | grep -E \"cognitive complexity of|too many lines|-->\" | paste - - | sed -E \"s/ *--> */ @ /\" | sort | tail -150" ;;
  *) echo "неизвестно: $WHAT"; exit 2 ;;
esac
# shellcheck disable=SC2029
set +e
if [ "$CHECK_HOST" = calc ]; then
  # TK-170: ядра слота (CALC_CORES, по умолчанию 4) и память — заявка планировщику; submit возвращает id, дальше ждём rc/<id>
  CORES="${CALC_CORES:-4}"; MEMG="${CALC_MEM:-12}"
  ssh "${KEY[@]}" "$HOST" "mkdir -p /opt/rbuild/in && cat > /opt/rbuild/run.sh.$$ && mv /opt/rbuild/run.sh.$$ /opt/rbuild/run.sh" < "$(dirname "$0")/calc-build-run.sh"   # раннер выкладывается с деревом: версий не расходится
  ID=$(ssh "${KEY[@]}" "$HOST" "mkdir -p /opt/rbuild/in && cat > /opt/rbuild/in/cmd-$$.sh <<'EOS'
export PATH=/opt/rbuild/cargo/bin:\$PATH RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo
set -o pipefail
$CMD
EOS
cd /opt/rbuild && SCHED_MANIFEST=0 python3 /data/sched/alsched.py submit --name build-$LABEL --max-runtime ${CALC_MAX:-15m} --cls prod --cores $CORES --mem $MEMG --prio 3 -- bash /opt/rbuild/run.sh $ARCR /opt/rbuild/in/cmd-$$.sh $CORES" | tail -1)
  case "$ID" in ''|*[!0-9]*) echo "alsched submit: id не получен ($ID)"; RC=75 ;; *)
    echo "alsched id $ID — wait_for: host:calc:/data/sched/rc/$ID (лог /data/sched/logs/$ID.log)"
    ssh "${KEY[@]}" "$HOST" "while [ ! -e /data/sched/rc/$ID ]; do sleep 2; done; cat /data/sched/logs/$ID.log; exit \$(cat /data/sched/rc/$ID)"
    RC=$? ;;
  esac
else
ssh "${KEY[@]}" "$HOST" "set -o pipefail; rm -rf $SRC && mkdir -p $SRC && tar -xzf $ARCR -C $SRC   && cd $SRC && export PATH=\$HOME/.cargo/bin:\$PATH && flock -w 7200 /opt/alpha-compute/.build.lock env ALPHA_TICKET=$LABEL nice -n 5 /opt/alpha-compute/sweep.sh run $TGT bash -c '$CMD'"
RC=$?
fi
set -e
# шина событий (TK-045): сборка готова/упала; недоступность шины результат не меняет
python "$(dirname "$0")/bus/busclient.py" send "сборка.$([ $RC -eq 0 ] && echo готова || echo упала)"   --payload "{\"tree\":\"$(basename "$PWD")\",\"what\":\"$WHAT\",\"ticket\":\"${RPV_TICKET:-${ALPHA_TICKET:-}}\",\"rc\":$RC}" >/dev/null || true
exit $RC
