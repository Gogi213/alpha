#!/bin/bash
# Выкладка «Настроек v2» вкладки «Арбитраж» (page-dense.html + круги «до нуля» и «из минуса в плюс» по θ, котировка ноги 60 с, без net250) на srv, страницы 8088 / 8089.
# Копии *.prev-dense (15.11) и *.prev-circles (16.11) не трогаются: откат этой выкладки — отдельный суффикс *.prev-circles.
#
#   bash deploy_circles.sh deploy --yes   выкладка по шагам; после первой правки боевого — автооткат при сбое
#   bash deploy_circles.sh rollback       вернуть state.pkl, код и page.html из копий *.prev-circles, стартовать arb-live
#   bash deploy_circles.sh verify         проверка выложенного (строки, круги z / x, θ, оборот, страница «Настройки»)
#   bash deploy_circles.sh status         что сейчас на srv
#   bash deploy_circles.sh sofia          клик-тесты на Софии (1180 / 1280 / 1440) по живой странице; на ПК браузер не запускается
#
# Что кладётся — только то, что закоммичено в HEAD (git show): core.py, arb_inc.py, arb_live.py, analyze.py, arb_daemon.py (/opt/arb-tab/tools/arb_stats) и page.html (/data/turbo-probe/page).
# serve.py, arb_api.py, common.py, arb_turnover.py и др. на srv уже равны HEAD — проверяется md5 до выкладки; страницы 8088 / 8089 не перезапускаются
# (page.html читается по mtime), arb-turnover не перезапускается.
#
# Порядок deploy (боевое трогает только шаг 3):
#   0. проверки: нет /data/perf/HOLD, нет *.prev-circles, порты и службы живы, неизменяемые файлы srv равны HEAD, место на диске; md5 прежних файлов — в deploy-settings.manifest
#   1. копии *.prev-circles (код + page.html); код HEAD — в двойник ($TWIN/code) и в стейдж ($CODE_STAGE: полная копия каталога + файлы кода, проверка импорта arb_daemon / arb_turnover)
#   2. двойник: служба $TWIN_UNIT (nice 19) догоняет «сейчас» с сохранённого состояния двойника (чтение открытых файлов с начала ≈ 6 мин + проход); проверка arb.json двойника
#      (строки, круги z / x); служба останавливается, её state.pkl копируется рядом с боевым (state.pkl.new). Боевой счёт идёт как шёл.
#   3. swap (секунды): stop arb-live → state.pkl.prev-circles, state.pkl.new → state.pkl, файлы кода на место → start arb-live; ждёт первого прохода (чтение открытых файлов с начала
#      ≈ 6 мин: страница на это время показывает прежний arb.json, старее на минуты); проверка arb.json (круги z / x)
#   4. page.html (после первого нового arb.json — столбцы кругов не пустые)
#   5. проверка
# Переменные: SRV, SSH_KEY, SSH_KNOWN_HOSTS (кириллица в HOME — ключ и known_hosts всегда явно), SOFIA.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SRV="${SRV:-root@5.231.25.224}"
SOFIA="${SOFIA:-root@13.140.29.171}"
KEY="${SSH_KEY:-/c/Users/Георгий/.ssh/id_rsa}"
KH="${SSH_KNOWN_HOSTS:-/c/Users/Георгий/.ssh/known_hosts}"
PAGE=/data/turbo-probe/page
CODE=/opt/arb-tab/tools/arb_stats
CODE_STAGE=/opt/arb-tab/tools/.stage-circles
LIVE=/data/arb-tab/live
TWIN="${TWIN:-/data/arb-v2}"            # двойник: код $TWIN/code/tools/arb_stats, счёт $TWIN/live (их состояние и сырые файлы — общие с боевым)
TWIN_UNIT=circles-twin
LIVE_UNIT=arb-live
PAGES="health-page-8088 health-soak-page"
PORTS="8088 8089"
FIRST_PORT="${PORTS%% *}"
TAG=prev-circles
MAN="$PAGE/deploy-circles.manifest"
CODE_FILES="core.py arb_inc.py arb_live.py analyze.py arb_daemon.py"
SAME_CODE="common.py arb_turnover.py parse_trades.py parse_inc.py"
SAME_PAGE="serve.py arb_api.py"
PAGE_SRC=tools/collector_health/page-dense.html

sshx() { ssh -i "$KEY" -o UserKnownHostsFile="$KH" -o BatchMode=yes -o ConnectTimeout=15 "$SRV" "$@"; }
sshs() { ssh -i "$KEY" -o UserKnownHostsFile="$KH" -o BatchMode=yes -o ConnectTimeout=15 "$SOFIA" "$@"; }
say() { echo "[$(date +%H:%M:%S)] $*"; }
blob() { git -C "$REPO" show "HEAD:$1"; }
md5l() { blob "$1" | md5sum | cut -c1-32; }

status() {
  sshx "set -e
    for u in $LIVE_UNIT arb-turnover $PAGES $TWIN_UNIT; do printf '%-20s %s\n' \$u \$(systemctl is-active \$u || true); done
    md5sum $PAGE/page.html $(for f in $CODE_FILES; do printf '%s ' $CODE/\$f; done) | cut -c1-12,33-
    ls $PAGE/*.$TAG $CODE/*.$TAG $LIVE/state.pkl.$TAG 2>/dev/null || echo 'копий $TAG нет'
    ls -la --time-style=+%F_%T $LIVE/arb.json $LIVE/state.pkl | cut -c1-120
    journalctl -u $LIVE_UNIT -n 2 --no-pager 2>&1 | cut -c1-330 || true"
}

# проверка arb.json боевого каталога: строки, круги z / x, θ, оборот, net250 нет; MINROWS — минимум строк (после ограничения котировки 60 с у малоликвидных пар строк меньше, чем было)
check_arb() {
  local port="$1"
  sshx "python3 - $port ${MINROWS:-2000}" <<'PY'
import json, sys, time, urllib.request
port, minrows = sys.argv[1], int(sys.argv[2])
d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/arb", timeout=30))
r = d["rows"]
nn = sum(1 for x in r if x.get("net"))
nz = sum(1 for x in r if x.get("z"))
nx = sum(1 for x in r if x.get("x"))
n250 = sum(1 for x in r if "net250" in x)
v = sum(1 for x in r if "v" in x)
age = round(time.time() - d["written"] / 1000)
print(f"порт {port}: строк {len(r)}, с закрытыми эпизодами {nn}, круги до нуля {nz}, из − в + {nx}, net250 {n250}, thetas {d.get('thetas')}, с оборотом {v}, возраст arb.json {age} с, gen {d['gen']}")
sys.exit(0 if len(r) >= minrows and nn and nz and nx and not n250 and d.get("thetas") == [10.0, 15.0, 20.0, 30.0, 50.0] and v >= 0.9 * len(r) else 1)
PY
}

verify() {
  say "проверка выложенного"
  for port in $PORTS; do
    check_arb "$port" || { say "FAIL: /api/arb на $port"; return 1; }
    code="$(sshx "curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:$port/fonts/geist-latin-wght-normal.woff2")"
    [ "$code" = "200" ] || { say "FAIL: /fonts на $port → $code"; return 1; }
    sshx "curl -s --max-time 20 http://127.0.0.1:$port/ | grep -q 'Настройки'" || { say "FAIL: на $port нет «Настройки»"; return 1; }
    sshx "curl -s --max-time 20 http://127.0.0.1:$port/ | grep -q 'alpha.arb.theta'" || { say "FAIL: на $port страница без θ"; return 1; }
    say "порт $port: страница с «Настройки» и θ, шрифты 200"
  done
  want="$(md5l $PAGE_SRC)"; have="$(sshx "md5sum $PAGE/page.html | cut -c1-32")"
  [ "$want" = "$have" ] || { say "FAIL: page.html $have, в HEAD $want"; return 1; }
  say "page.html = HEAD ($have)"
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 turnover_verify.py http://127.0.0.1:$FIRST_PORT/api/arb BTC,SOL,ZORA"
}

rollback() {
  set +e; trap - ERR
  say "откат"
  sshx "set -e
    systemctl stop $TWIN_UNIT 2>/dev/null || true
    if [ -e $LIVE/state.pkl.$TAG ]; then
      systemctl stop $LIVE_UNIT
      cp -p $LIVE/state.pkl.$TAG $LIVE/state.pkl.rolling && mv $LIVE/state.pkl.rolling $LIVE/state.pkl && rm -f $LIVE/state.pkl.$TAG && echo 'вернул state.pkl'
    fi
    for f in $CODE_FILES; do [ -e $CODE/\$f.$TAG ] && cp -p $CODE/\$f.$TAG $CODE/\$f.rolling && mv $CODE/\$f.rolling $CODE/\$f && rm -f $CODE/\$f.$TAG && echo \"вернул \$f\"; done
    [ -e $PAGE/page.html.$TAG ] && cp -p $PAGE/page.html.$TAG $PAGE/page.html.rolling && mv $PAGE/page.html.rolling $PAGE/page.html && rm -f $PAGE/page.html.$TAG && echo 'вернул page.html'
    rm -rf $CODE_STAGE $LIVE/state.pkl.new
    systemctl start $LIVE_UNIT
    sleep 2
    printf '%-20s %s\n' $LIVE_UNIT \$(systemctl is-active $LIVE_UNIT)"
  for kv in page:$PAGE/page.html core:$CODE/core.py arb_inc:$CODE/arb_inc.py live:$CODE/arb_live.py analyze:$CODE/analyze.py daemon:$CODE/arb_daemon.py; do
    b="$(sshx "grep '^${kv%%:*}_md5=' $MAN 2>/dev/null | cut -d= -f2" || true)"; n="$(sshx "md5sum ${kv#*:} | cut -c1-32")"
    if [ -n "$b" ] && [ "$b" = "$n" ]; then say "${kv#*:} совпадает с записанным до выкладки"; else say "ВНИМАНИЕ: md5 ${kv#*:} $n, до выкладки было '${b:-нет записи}'"; fi
  done
  say "arb-live прочитает открытые файлы с начала (≈ 6 мин) и допишет arb.json; копии *.prev-dense не тронуты"
}

# сбой до swap: боевое не менялось — убрать копии и остановить двойник
abort_pre() {
  set +e; trap - ERR
  say "СБОЙ до swap — боевое не менялось; убираю копии $TAG и стейдж"
  sshx "systemctl stop $TWIN_UNIT 2>/dev/null; rm -rf $CODE_STAGE $LIVE/state.pkl.new
    for f in $CODE_FILES; do rm -f $CODE/\$f.$TAG; done; rm -f $PAGE/page.html.$TAG"
}

deploy() {
  [ "${1:-}" = "--yes" ] || { echo "нужен явный флаг: deploy --yes"; exit 2; }
  git -C "$REPO" diff --quiet HEAD -- tools/arb_stats "$PAGE_SRC" tools/collector_health/serve.py tools/collector_health/arb_api.py || { echo "в tools/arb_stats или странице есть незакоммиченные правки — кладётся только HEAD"; exit 1; }
  HEAD_SHA="$(git -C "$REPO" rev-parse --short HEAD)"
  say "шаг 0: проверки (HEAD $HEAD_SHA)"
  { for f in $SAME_CODE; do echo "$(md5l tools/arb_stats/$f)  $CODE/$f"; done; for f in $SAME_PAGE; do echo "$(md5l tools/collector_health/$f)  $PAGE/$f"; done; } | sshx "md5sum -c --quiet -" \
    || { echo "файлы srv, которые не выкладываются, не равны HEAD"; exit 1; }
  sshx "set -e
    [ ! -e /data/perf/HOLD ] || { echo 'есть /data/perf/HOLD — идёт замер, выкладка отложена'; exit 1; }
    [ ! -e $PAGE/page.html.$TAG ] && [ ! -e $LIVE/state.pkl.$TAG ] && [ ! -e $CODE_STAGE ] && [ ! -e $LIVE/state.pkl.new ] || { echo 'копии $TAG / стейдж уже есть — сначала rollback'; exit 1; }
    for f in $CODE_FILES; do [ ! -e $CODE/\$f.$TAG ] || { echo \"уже есть $CODE/\$f.$TAG — сначала rollback\"; exit 1; }; done
    for p in $(echo $PORTS); do curl -sf --max-time 20 http://127.0.0.1:\$p/api/arb -o /dev/null || { echo \"порт \$p не отвечает\"; exit 1; }; done
    for u in $LIVE_UNIT arb-turnover; do systemctl is-active \$u >/dev/null || { echo \"\$u не работает\"; exit 1; }; done
    ! systemctl is-active $TWIN_UNIT >/dev/null 2>&1 || { echo '$TWIN_UNIT уже работает'; exit 1; }
    [ -s $TWIN/live/state.pkl ] && [ -d $TWIN/code/tools/arb_stats ] || { echo 'нет двойника $TWIN'; exit 1; }
    [ \$(df --output=avail -BG /data | tail -1 | tr -dc 0-9) -ge 10 ] || { echo 'на /data меньше 10 ГБ'; exit 1; }"
  say "шаг 1: копии *.$TAG, код HEAD в двойник и стейдж"
  trap 'abort_pre; exit 1' ERR
  sshx "set -e
    { echo \"at=\$(date -Is)\"; echo \"head=$HEAD_SHA\"; echo \"page_md5=\$(md5sum $PAGE/page.html | cut -c1-32)\"
      echo \"core_md5=\$(md5sum $CODE/core.py | cut -c1-32)\"; echo \"arb_inc_md5=\$(md5sum $CODE/arb_inc.py | cut -c1-32)\"
      echo \"live_md5=\$(md5sum $CODE/arb_live.py | cut -c1-32)\"; echo \"analyze_md5=\$(md5sum $CODE/analyze.py | cut -c1-32)\"; echo \"daemon_md5=\$(md5sum $CODE/arb_daemon.py | cut -c1-32)\"; } > $MAN
    for f in $CODE_FILES; do cp -p $CODE/\$f $CODE/\$f.$TAG; done
    cp -p $PAGE/page.html $PAGE/page.html.$TAG"
  git -C "$REPO" archive HEAD tools/arb_stats | sshx "tar --no-same-owner -xf - -C $TWIN/code && cd $TWIN/code/tools/arb_stats && PYTHONPATH=/opt/arb-tab/pylib python3 -m py_compile $CODE_FILES arb_daemon.py"
  sshx "set -e; mkdir -p $CODE_STAGE; cp -p $CODE/*.py $CODE_STAGE/"
  git -C "$REPO" archive HEAD tools/arb_stats | sshx "tar --no-same-owner -xf - -C $CODE_STAGE --strip-components=2 $(for f in $CODE_FILES; do printf 'tools/arb_stats/%s ' $f; done)"
  for f in $CODE_FILES; do
    want="$(md5l tools/arb_stats/$f)"; have="$(sshx "md5sum $CODE_STAGE/$f | cut -c1-32")"
    [ "$want" = "$have" ] || { say "стейдж: $f $have, в HEAD $want"; false; }
  done
  sshx "cd $CODE_STAGE && PYTHONPATH=/opt/arb-tab/pylib python3 -c 'import arb_daemon, arb_turnover, arb_live, analyze, core, arb_inc; print(\"импорт стейджа: ok\")'"
  say "шаг 2: двойник догоняет «сейчас» (первый проход ≈ 6 мин: чтение открытых файлов с начала)"
  t0="$(sshx 'date +%s')"
  sshx "systemctl reset-failed $TWIN_UNIT 2>/dev/null || true; : > $TWIN/deploy-run.log
    systemd-run --unit=$TWIN_UNIT --nice=19 -p CPUWeight=20 -p CPUQuota=300% -p MemoryMax=16G -p WorkingDirectory=$TWIN/code/tools/arb_stats -E PYTHONPATH=/opt/arb-tab/pylib \
      -p StandardOutput=file:$TWIN/deploy-run.log -p StandardError=inherit /usr/bin/python3 $TWIN/code/tools/arb_stats/arb_daemon.py run $TWIN/live 3 >/dev/null"
  ok=0
  for i in $(seq 1 90); do
    sleep 20
    sshx "systemctl is-active $TWIN_UNIT >/dev/null" || { say "двойник упал"; sshx "tail -n 15 $TWIN/deploy-run.log | cut -c1-300; journalctl -u $TWIN_UNIT -n 10 --no-pager | cut -c1-260"; false; }
    n="$(sshx "grep -c '\"event\": \"pass\"' $TWIN/deploy-run.log || true")"
    [ "${n:-0}" -ge 1 ] && { ok=1; break; }
  done
  [ "$ok" = "1" ] || { say "двойник не дал прохода за 30 мин"; false; }
  sshx "systemctl stop $TWIN_UNIT; grep '\"event\": \"pass\"' $TWIN/deploy-run.log | head -1 | cut -c1-330"
  sshx "python3 - $TWIN/live/arb.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["rows"]
nn = sum(1 for x in r if x.get("net")); nz = sum(1 for x in r if x.get("z")); nx = sum(1 for x in r if x.get("x"))
print(f"двойник: строк {len(r)}, с закрытыми эпизодами {nn}, круги до нуля {nz}, из − в + {nx}, t1 {d['t1']}")
sys.exit(0 if len(r) >= 2000 and nn and nz and nx else 1)
PY
  sshx "set -e; cp $TWIN/live/state.pkl $LIVE/state.pkl.new; cmp $TWIN/live/state.pkl $LIVE/state.pkl.new && echo 'state.pkl.new готов'"
  say "шаг 3: swap (stop arb-live → state.pkl → код → start)"
  trap 'say "СБОЙ после swap — автооткат"; rollback; exit 1' ERR
  t0="$(sshx 'date +%s')"
  sshx "set -e
    systemctl stop $LIVE_UNIT
    cp -p $LIVE/state.pkl $LIVE/state.pkl.$TAG
    mv $LIVE/state.pkl.new $LIVE/state.pkl
    for f in $CODE_FILES; do mv $CODE_STAGE/\$f $CODE/\$f; done
    systemctl start $LIVE_UNIT"
  say "swap сделан; жду первый проход $LIVE_UNIT (до 25 мин)"
  ok=0
  for i in $(seq 1 75); do
    sleep 20
    sshx "systemctl is-active $LIVE_UNIT >/dev/null" || { say "$LIVE_UNIT упал"; sshx "journalctl -u $LIVE_UNIT -n 15 --no-pager | cut -c1-260"; false; }
    ok="$(sshx "[ \$(stat -c %Y $LIVE/arb.json) -ge $t0 ] && echo 1 || echo 0")"
    [ "$ok" = "1" ] && break
  done
  [ "$ok" = "1" ] || { say "arb.json не обновился за 25 мин"; false; }
  sshx "journalctl -u $LIVE_UNIT --since @$t0 --no-pager | grep '\"event\": \"pass\"' | head -2 | cut -c1-330"
  check_arb "$FIRST_PORT"
  say "шаг 4: page.html"
  blob "$PAGE_SRC" | sshx "cat > $PAGE/page.html.new && mv $PAGE/page.html.new $PAGE/page.html"
  sshx "rm -rf $CODE_STAGE"
  say "шаг 5: проверка"
  verify
  trap - ERR
  say "готово ($HEAD_SHA). Откат: bash tools/collector_health/deploy_circles.sh rollback"
}

# клик-тесты на Софии (Chrome, playwright-core 1.48.2 для node 18 в /root/arb-settings-test): тесты — из HEAD
sofia() {
  local url="${1:-http://${SRV#*@}:$FIRST_PORT/}"
  say "тесты на Софии: $url"
  for t in settings_test layout_test_cols layout_test_shadcn exchange_chips_test; do
    git -C "$REPO" show "HEAD:tools/collector_health/perf/$t.js" | sshs "cat > /root/arb-settings-test/$t.js"
  done
  sshs "cd /root/arb-settings-test && export PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome
    for w in 1180 1280 1440; do for t in settings_test layout_test_cols layout_test_shadcn exchange_chips_test; do
      echo \"== \$t \$w\"; node \$t.js $url \$w 2>&1 | tail -8
    done; done"
}

case "${1:-}" in
  deploy) shift; deploy "$@" ;;
  rollback) rollback ;;
  verify) verify ;;
  status) status ;;
  sofia) shift; sofia "$@" ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
