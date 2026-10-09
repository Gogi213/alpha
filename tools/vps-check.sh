#!/usr/bin/env bash
# Сборка и проверки рабочего дерева на VPS (13.140.29.171) — вторая дорожка работ, пока на этой машине идёт
# своя сборка (владелец 24.09: «одна сборка здесь, вторая на VPS»; две сборки на одной машине — нельзя).
#   tools/vps-check.sh <рабочее дерево> [test|clippy|fmt|build|all|gate|complexity] [фильтр тестов]
# Переносит отслеживаемые и новые файлы дерева (без target*/data/) архивом с сохранением времени изменения —
# неизменённые файлы cargo не пересобирает; сборка — в отдельном target-wave2, не в каталоге сборок для дека.
# Сборки на VPS идут по очереди: замок /opt/alpha-compute/.build.lock (его же берёт сборка бинарника для дека).
set -euo pipefail
TREE="${1:?рабочее дерево}"
WHAT="${2:-all}"
FILTER="${3:-}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30)
HOST=root@13.140.29.171
TAG="${VPS_TAG:-}"   # непустой тег — свой каталог исходников и target: параллельные вызовы не затирают друг друга
SRC=/opt/alpha-compute/wave2-src$TAG
TGT=/opt/alpha-compute/target-wave2$TAG-$$   # TK-075: target живёт одну сборку, sweep.sh run удаляет его после
ARCR=/opt/alpha-compute/wave2$TAG.tgz
TMPD="${TEMP:-/tmp}"; command -v cygpath >/dev/null && TMPD="$(cygpath -u "$TMPD")"
ARC="$TMPD/wave2-$$.tgz"
cd "$TREE"
# метка задачи в командной строке замка (видна в `ps` на VPS — экран хода работ по ней находит задачу сборки):
# ALPHA_TICKET сессии роли (его ставит диспетчер), иначе имя рабочего дерева; на поведение сборки не влияет
LABEL="$(printf '%s' "${ALPHA_TICKET:-$(basename "$PWD")}" | tr -c 'A-Za-z0-9._-' '_')"
git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
scp -q "${KEY[@]}" "$ARC" "$HOST:$ARCR"
rm -f "$ARC"
# cargo-фичи (TK-065: R2 — `VPS_FEATURES=r2`); пусто = боевой набор без фич
FEAT="${VPS_FEATURES:+--features $VPS_FEATURES}"
# fmt + clippy -D warnings + тесты с набором фич $1 (пусто — без фич)
all_cmd() {
  echo "set -o pipefail; cargo fmt --check 2>&1 | tail -10 && cargo clippy --release $1 --target-dir $TGT --all-targets -j 3 -- -D warnings 2>&1 | tail -15 && cargo test --release $1 --target-dir $TGT -j 3 2>&1 | grep -E \"^test result|FAILED|panicked|^error|^warning: unused\" | tail -40"
}
case "$WHAT" in
  test)  CMD="cargo test --release $FEAT --target-dir $TGT -j 3 $FILTER 2>&1 | grep -E \"^test result|FAILED|panicked|^error|^warning: unused\" | tail -40" ;;
  clippy) CMD="cargo clippy --release $FEAT --target-dir $TGT --all-targets -j 3 -- -D warnings 2>&1 | tail -25" ;;
  fmt)    CMD="cargo fmt --check 2>&1 | tail -25" ;;
  build)  CMD="cargo build --release $FEAT --target-dir $TGT -j 3 2>&1 | tail -5" ;;
  all)    CMD="$(all_cmd "$FEAT")" ;;
  # TK-132 С-02: гейт R2 — второй бинарник (feature r2) гоняет 14+4 R2-теста, которые `all` без фич не видит:
  # `gate` = `all` без фич, затем то же с `--features r2`
  gate)   CMD="$(all_cmd "") && $(all_cmd "--features r2")" ;;
  # TK-132 (А1 §0): настоящая метрика — clippy cognitive_complexity (порог 15) и too_many_lines; код не меняется,
  # clippy.toml пишется только в одноразовую копию дерева на VPS. Вывод: «функция @ файл:строка», по алфавиту
  complexity) CMD="printf 'cognitive-complexity-threshold = 15\n' > clippy.toml && cargo clippy --release $FEAT --target-dir $TGT --all-targets -j 3 -- -W clippy::cognitive_complexity -W clippy::too_many_lines 2>&1 | grep -E -A1 \"cognitive complexity of|too many lines\" | grep -E \"cognitive complexity of|too many lines|-->\" | paste - - | sed 's/ *--> */ @ /' | sort | tail -150" ;;
  *) echo "неизвестно: $WHAT"; exit 2 ;;
esac
# shellcheck disable=SC2029
set +e
ssh "${KEY[@]}" "$HOST" "set -o pipefail; rm -rf $SRC && mkdir -p $SRC && tar -xzf $ARCR -C $SRC \
  && cd $SRC && export PATH=\$HOME/.cargo/bin:\$PATH && flock -w 7200 /opt/alpha-compute/.build.lock env ALPHA_TICKET=$LABEL nice -n 5 /opt/alpha-compute/sweep.sh run $TGT bash -c '$CMD'"
RC=$?
set -e
# шина событий (TK-045): сборка готова/упала; недоступность шины результат не меняет
python "$(dirname "$0")/bus/busclient.py" send "сборка.$([ $RC -eq 0 ] && echo готова || echo упала)"   --payload "{\"tree\":\"$(basename "$PWD")\",\"what\":\"$WHAT\",\"ticket\":\"${ALPHA_TICKET:-}\",\"rc\":$RC}" >/dev/null || true
exit $RC
