#!/bin/bash
# Выкладка плотной вкладки «Арбитраж» (page-dense.html + оборот за сутки + шрифты Geist) на srv, страницы 8088 / 8089.
# НЕ ЗАПУСКАТЬ без слова владельца «да» на финальный макет (владелец 15.11: макет ещё не принят).
#
#   bash deploy_dense.sh deploy --yes   выкладка по шагам, при сбое после первой правки — автооткат
#   bash deploy_dense.sh rollback       вернуть прежние файлы, остановить arb-turnover, перезапустить страницы
#   bash deploy_dense.sh verify         проверка выложенного (/api/arb с оборотом, шрифты, 3 монеты против тикеров бирж)
#   bash deploy_dense.sh status         что сейчас на srv
#   bash deploy_dense.sh twin up|down   песочница репетиции (порт 8090, каталоги /data/arb-shadcn/rh, юниты rh-page-8090 и arb-live-rh); боевое не затрагивает
#   bash deploy_dense.sh gate --yes     только Gate (5-я биржа) в живой счёт: пересборка состояния + рестарт arb-live (deploy делает это сам; GATE=0 — без Gate)
#
# Порядок deploy (arb-live не перезапускается — рестарт прогревает все файлы книг с начала; страницы перезапускаются на ~1 с):
#   0. проверки: нет /data/perf/HOLD, копий *.prev-dense ещё нет, страницы отвечают; запись прежних md5 в deploy-dense.manifest
#   1. копии *.prev-dense: page.html, serve.py, arb_api.py (/data/turbo-probe/page), arb_daemon.py (/opt/arb-tab/tools/arb_stats)
#   2. код службы оборота (arb_turnover.py, parse_trades.py, arb_daemon.py с Reader.consume) + юнит arb-turnover.service — запуск ПЕРВЫМ:
#      bootstrap ≈ 4–5 мин на 3 ядрах, turnover.json появляется до перезапуска страниц
#   3. serve.py + arb_api.py + шрифты → перезапуск 8088 / 8089; проверка /fonts и v в /api/arb
#   3б. Gate (шаг gate_step, пропуск — GATE=0): arb-live НЕ может подхватить новую биржу на лету (в процессе EXCHANGES без Gate, истории Gate в состоянии нет) —
#      состояние строится заново по сырым файлам пяти бирж в пустом каталоге ($LIVE_GATE: arb_daemon.py rebuild, ≈ 15–25 мин на 3 ядрах, nice 19; боевой счёт идёт как шёл),
#      затем arb-live останавливается, state.pkl кладётся на место (прежний — state.pkl.prev-dense), служба стартует, догоняет с t_a пересборки (чтение открытых файлов с начала до
#      сохранённых позиций + проход за минуты пересборки) и пишет arb.json с парами «… → Gate». Страница на это время показывает прежний arb.json (старее на минуты). Ждёт первого прохода до 30 мин.
#   4. page.html (читается по mtime — вступает в силу сразу)
#   5. проверка: оборот 3 монет (BTC, SOL, ZORA) против тикеров бирж; при LAYOUT=1 — клик-тест страницы с этого ПК
# Переменные: SRV (root@5.231.25.224), SSH_KEY, SSH_KNOWN_HOSTS (кириллица в HOME — ключ и known_hosts всегда явно), LAYOUT=1.
# Репетиция без боя: PAGE, CODE, LIVE, UNIT, PAGES, PORTS, PRE_UNITS, LIVE_UNIT, LIVE_GATE (+ REBUILT — готовый каталог пересборки, шаг rebuild пропускается) переопределяются
# (каталоги и юниты-двойники, порт 8090: юнит arb-live-rh — копия arb-live с путями двойника, см. rehearse_units) — боевые 8088/8089 и arb-live не затрагиваются.
# Откат (rollback) идёт в обратном порядке: arb-live останавливается, state.pkl.prev-dense и код *.prev-dense возвращаются, arb-live стартует (старый процесс — без Gate).
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SRV="${SRV:-root@5.231.25.224}"
KEY="${SSH_KEY:-/c/Users/Георгий/.ssh/id_rsa}"
KH="${SSH_KNOWN_HOSTS:-/c/Users/Георгий/.ssh/known_hosts}"
PAGE="${PAGE:-/data/turbo-probe/page}"
CODE="${CODE:-/opt/arb-tab/tools/arb_stats}"
LIVE="${LIVE:-/data/arb-tab/live}"
TAG=prev-dense
MAN="$PAGE/deploy-dense.manifest"
UNIT="${UNIT:-arb-turnover}"
PAGES="${PAGES:-health-page-8088 health-soak-page}"
PORTS="${PORTS:-8088 8089}"
LIVE_UNIT="${LIVE_UNIT:-arb-live}"          # служба живого счёта: рестарт нужен только для Gate (шаг gate_step)
PRE_UNITS="${PRE_UNITS:-$LIVE_UNIT}"        # должны работать до выкладки
LIVE_GATE="${LIVE_GATE:-${LIVE}-gate}"      # пустой каталог пересборки состояния (на том же диске, что $LIVE)
REBUILD_UNIT="${REBUILD_UNIT:-gate-rebuild}"
REBUILD_CMD="${REBUILD_CMD:-python3 $CODE/arb_daemon.py rebuild $LIVE_GATE 4}"     # подмена — только для проверки ожидания в репетиции
FIRST_PORT="${PORTS%% *}"
BOOT_QUOTA="${BOOT_QUOTA:-300%}"            # на время bootstrap службе дана квота 3 ядра (в юните — 100 %, для обычных проходов)
PAGE_FILES="serve.py arb_api.py page.html"
CODE_BACKUP="arb_daemon.py common.py arb_live.py analyze.py"     # заменяются (с Gate: EXCHANGES, quanto_multiplier, MINRUN по биржам); копии *.prev-dense
CODE_NEW="arb_turnover.py parse_trades.py turnover_check.py turnover_verify.py state_report.py"

code_paths() { for f in $CODE_BACKUP; do printf '%s ' "$CODE/$f"; done; }
sshx() { ssh -i "$KEY" -o UserKnownHostsFile="$KH" -o BatchMode=yes -o ConnectTimeout=15 "$SRV" "$@"; }
say() { echo "[$(date +%H:%M:%S)] $*"; }

status() {
  sshx "set -e
    for u in $UNIT $PAGES $PRE_UNITS; do printf '%-20s %s\n' \$u \$(systemctl is-active \$u); done
    md5sum $PAGE/page.html $PAGE/serve.py $PAGE/arb_api.py $(code_paths) | cut -c1-12,33-
    ls $PAGE/*.$TAG $CODE/*.$TAG $LIVE/state.pkl.$TAG 2>/dev/null || echo 'копий $TAG нет'
    ls -la $LIVE/turnover.json $LIVE/turnover-state.pkl 2>&1 | cut -c1-120
    journalctl -u $UNIT -n 3 --no-pager 2>&1 | cut -c1-240 || true"
}

rollback() {
  set +e; trap - ERR
  say "откат"
  sshx "set -e
    swapped=
    if [ -e $LIVE/state.pkl.$TAG ]; then      # Gate успел встать: прежнее состояние счёта на место при остановленной службе
      systemctl stop $LIVE_UNIT
      cp -p $LIVE/state.pkl.$TAG $LIVE/state.pkl.rolling && mv $LIVE/state.pkl.rolling $LIVE/state.pkl && rm -f $LIVE/state.pkl.$TAG && echo 'вернул state.pkl' && swapped=1
    fi
    for f in $PAGE_FILES; do [ -e $PAGE/\$f.$TAG ] && cp -p $PAGE/\$f.$TAG $PAGE/\$f.rolling && mv $PAGE/\$f.rolling $PAGE/\$f && rm -f $PAGE/\$f.$TAG && echo \"вернул \$f\"; done
    for f in $CODE_BACKUP; do [ -e $CODE/\$f.$TAG ] && cp -p $CODE/\$f.$TAG $CODE/\$f.rolling && mv $CODE/\$f.rolling $CODE/\$f && rm -f $CODE/\$f.$TAG && echo \"вернул \$f\"; done
    systemctl stop $UNIT 2>/dev/null || true
    systemctl disable $UNIT 2>/dev/null || true
    rm -f /etc/systemd/system/$UNIT.service; systemctl daemon-reload
    for f in $CODE_NEW; do rm -f $CODE/\$f; done
    rm -rf $PAGE/fonts
    systemctl restart $PAGES
    [ -z \"\$swapped\" ] || systemctl start $LIVE_UNIT
    sleep 2
    for u in $PAGES $LIVE_UNIT; do printf '%-20s %s\n' \$u \$(systemctl is-active \$u); done"
  before="$(sshx "grep '^page_md5=' $MAN 2>/dev/null | cut -d= -f2" || true)"
  now="$(sshx "md5sum $PAGE/page.html | cut -c1-32")"
  if [ -n "$before" ] && [ "$before" = "$now" ]; then say "page.html совпадает с записанным до выкладки ($before)"; else say "ВНИМАНИЕ: md5 page.html $now, до выкладки было '${before:-нет записи}'"; fi
  for kv in daemon:arb_daemon.py common:common.py live:arb_live.py analyze:analyze.py; do
    b="$(sshx "grep '^${kv%%:*}_md5=' $MAN 2>/dev/null | cut -d= -f2" || true)"; n="$(sshx "md5sum $CODE/${kv#*:} | cut -c1-32")"
    if [ -n "$b" ] && [ "$b" = "$n" ]; then say "${kv#*:} совпадает с записанным до выкладки"; else say "ВНИМАНИЕ: md5 ${kv#*:} $n, до выкладки было '${b:-нет записи}'"; fi
  done
  say "turnover.json и turnover-state.pkl в $LIVE оставлены (старый arb_api их не читает); каталог пересборки $LIVE_GATE (если остался) — удалить вручную"
}

verify() {
  say "проверка выложенного"
  for port in $PORTS; do
    code="$(sshx "curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:$port/fonts/geist-latin-wght-normal.woff2")"
    [ "$code" = "200" ] || { say "FAIL: /fonts на $port → $code"; return 1; }
    sshx "curl -s --max-time 20 http://127.0.0.1:$port/ | grep -q 'Столбцы'" || { say "FAIL: на $port не плотная страница"; return 1; }
    say "порт $port: страница плотная, шрифты 200"
  done
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 turnover_verify.py http://127.0.0.1:$FIRST_PORT/api/arb BTC,SOL,ZORA"
  if [ "${LAYOUT:-0}" = "1" ]; then
    say "клик-тест страницы с этого ПК (1280, 77 действий + «Столбцы»)"
    node "$HERE/perf/layout_test_shadcn.js" "http://${SRV#*@}:$FIRST_PORT/" 1280
    node "$HERE/perf/layout_test_cols.js" "http://${SRV#*@}:$FIRST_PORT/" 1280
  fi
}

# --- песочница репетиции: двойник страницы (порт 8090) и счёта (arb-live-rh) на копиях боевых файлов ----------------------------------------------------------------------
# Репетиция: twin up; затем с теми же PAGE/CODE/... (см. twin_env) deploy --yes / verify / rollback; twin down.
twin_env() { echo "PAGE=/data/arb-shadcn/rh/page CODE=/data/arb-shadcn/rh/code LIVE=/data/arb-shadcn/rh/live UNIT=arb-turnover-rh PAGES=rh-page-8090 PORTS=8090 LIVE_UNIT=arb-live-rh LIVE_GATE=/data/arb-shadcn/rh/live-gate"; }
twin() {
  case "${1:-}" in
    up)
      sshx "set -e
        R=/data/arb-shadcn/rh
        systemctl stop rh-page-8090 arb-live-rh arb-turnover-rh 2>/dev/null || true
        rm -rf \$R/code \$R/page \$R/live \$R/live-gate; mkdir -p \$R/code \$R/page \$R/live
        cp -p /opt/arb-tab/tools/arb_stats/*.py \$R/code/
        cp -p /data/turbo-probe/page/*.py /data/turbo-probe/page/*.json /data/turbo-probe/page/page.html \$R/page/
        cp -p /data/arb-tab/live/state.pkl /data/arb-tab/live/arb.json \$R/live/
        cp -p /data/arb-tab/live/\$(python3 -c 'import json; print(json.load(open(\"/data/arb-tab/live/arb.json\"))[\"chart\"])') \$R/live/
        sed \"s#/opt/arb-tab/tools/arb_stats#\$R/code#g; s#/data/arb-tab/live#\$R/live#g; s#^Description=.*#Description=репетиция: двойник arb-live (rh)#\" /etc/systemd/system/arb-live.service > /etc/systemd/system/arb-live-rh.service
        systemctl daemon-reload; systemctl start arb-live-rh
        systemd-run --unit=rh-page-8090 -p Restart=always /usr/bin/python3 \$R/page/serve.py --health-dir /data/turbo-probe/health-soak --port 8090 --exchanges \$R/page/exchanges.json --thresholds \$R/page/thresholds.json --arb-dir \$R/live >/dev/null
        sleep 3; for u in rh-page-8090 arb-live-rh; do printf '%-16s %s\n' \$u \$(systemctl is-active \$u); done"
      say "двойник поднят; переменные репетиции: $(twin_env)" ;;
    down)
      sshx "systemctl stop rh-page-8090 arb-live-rh arb-turnover-rh $REBUILD_UNIT 2>/dev/null; systemctl reset-failed 2>/dev/null; rm -f /etc/systemd/system/arb-live-rh.service /etc/systemd/system/arb-turnover-rh.service; systemctl daemon-reload; echo 'двойник остановлен; каталоги /data/arb-shadcn/rh* оставлены'" ;;
    *) echo "twin up|down"; exit 2 ;;
  esac
}

# --- Gate: пересборка состояния счёта и рестарт arb-live ---------------------------------------------------------------------------------------------------------------
gate_step() {
  say "Gate: пересборка состояния счёта (этап A по сырым файлам пяти бирж + bootstrap), nice 19, до 60 мин; боевой счёт идёт как шёл"
  local dir="${REBUILT:-$LIVE_GATE}"
  if [ -z "${REBUILT:-}" ]; then
    sshx "set -e
      [ ! -e $LIVE_GATE ] || { echo '$LIVE_GATE уже есть — удалить (или указать REBUILT=<каталог готовой пересборки>)'; exit 1; }
      [ \$(df --output=avail -BG /data | tail -1 | tr -dc 0-9) -ge 40 ] || { echo 'на /data меньше 40 ГБ — пересборка пишет ≈ 10 ГБ кусков'; exit 1; }
      mkdir -p $LIVE_GATE
      systemctl reset-failed $REBUILD_UNIT 2>/dev/null || true
      systemd-run --unit=$REBUILD_UNIT --nice=19 -p CPUWeight=20 -p CPUQuota=300% -p MemoryMax=24G -p RemainAfterExit=yes -E PYTHONPATH=/opt/arb-tab/pylib \
        -p StandardOutput=file:$LIVE_GATE/rebuild.log -p StandardError=inherit $REBUILD_CMD >/dev/null"
    for i in $(seq 1 120); do
      sleep 30
      st="$(sshx "systemctl show -p ActiveState --value $REBUILD_UNIT")"
      [ "$st" = "active" ] && { sshx "[ \$(systemctl show -p SubState --value $REBUILD_UNIT) = running ]" || break; } || break
    done
    r="$(sshx "systemctl show -p ExecMainStatus --value $REBUILD_UNIT; systemctl show -p SubState --value $REBUILD_UNIT" | tr '\n' ' ')"
    say "пересборка: код выхода / состояние: $r"
    sshx "tail -n 3 $LIVE_GATE/rebuild.log | cut -c1-300; systemctl stop $REBUILD_UNIT 2>/dev/null; true"
    [ "${r%% *}" = "0" ] || { say "пересборка не завершилась успешно"; false; }
  fi
  sshx "[ -s $dir/state.pkl ]" || { say "нет $dir/state.pkl"; false; }
  say "состояние пересборки (state_report):"
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 state_report.py $dir gate --brief"
  say "Gate: остановка $LIVE_UNIT, state.pkl на место (прежний — state.pkl.$TAG), старт"
  t0="$(sshx 'date +%s')"
  sshx "set -e
    systemctl stop $LIVE_UNIT
    cp -p $LIVE/state.pkl $LIVE/state.pkl.$TAG
    cp -p $dir/state.pkl $LIVE/state.pkl.new && mv $LIVE/state.pkl.new $LIVE/state.pkl
    systemctl start $LIVE_UNIT"
  say "жду первый проход $LIVE_UNIT с Gate (до 30 мин: чтение открытых файлов с начала до сохранённых позиций + догон минут пересборки)"
  ok=0
  for i in $(seq 1 60); do
    sleep 30
    sshx "systemctl is-active $LIVE_UNIT >/dev/null" || { say "$LIVE_UNIT упал"; sshx "journalctl -u $LIVE_UNIT -n 15 --no-pager | cut -c1-260"; false; }
    ok="$(sshx "[ \$(stat -c %Y $LIVE/arb.json) -ge $t0 ] && echo 1 || echo 0")"
    [ "$ok" = "1" ] && break
  done
  [ "$ok" = "1" ] || { say "arb.json не обновился за 30 мин"; false; }
  sshx "journalctl -u $LIVE_UNIT --since @$t0 --no-pager | grep '\"event\": \"pass\"' | head -2 | cut -c1-330; cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 state_report.py arbjson $LIVE/arb.json gate"
  if [ -z "${REBUILT:-}" ]; then sshx "rm -rf $LIVE_GATE"; say "каталог пересборки $LIVE_GATE удалён"; fi
  say "Gate: arb-live работает с пятью биржами"
}

gate_rollback() {
  set +e; trap - ERR
  say "Gate: откат состояния счёта (код на диске остаётся с Gate; полный возврат — rollback)"
  sshx "set -e
    if [ -e $LIVE/state.pkl.$TAG ]; then systemctl stop $LIVE_UNIT; cp -p $LIVE/state.pkl.$TAG $LIVE/state.pkl.rolling && mv $LIVE/state.pkl.rolling $LIVE/state.pkl && rm -f $LIVE/state.pkl.$TAG && echo 'вернул state.pkl'; fi
    systemctl start $LIVE_UNIT; sleep 2; printf '%-20s %s\n' $LIVE_UNIT \$(systemctl is-active $LIVE_UNIT)"
}

gate_cmd() {
  [ "${1:-}" = "--yes" ] || { echo "нужен явный флаг: gate --yes (после слова владельца «да»)"; exit 2; }
  sshx "set -e
    [ ! -e /data/perf/HOLD ] || { echo 'есть /data/perf/HOLD — идёт замер, отложено'; exit 1; }
    grep -q '\"gate\"' $CODE/common.py || { echo 'в $CODE/common.py нет Gate — сначала deploy (код выкладывается им) или GATE=0 deploy'; exit 1; }
    [ ! -e $LIVE/state.pkl.$TAG ] || { echo 'уже есть state.pkl.$TAG — сначала rollback'; exit 1; }
    systemctl is-active $LIVE_UNIT >/dev/null || { echo '$LIVE_UNIT не работает'; exit 1; }"
  trap 'say "СБОЙ — откат состояния"; gate_rollback; exit 1' ERR
  gate_step
  trap - ERR
}

deploy() {
  [ "${1:-}" = "--yes" ] || { echo "нужен явный флаг: deploy --yes (после слова владельца «да»)"; exit 2; }
  for f in $PAGE_FILES; do [ -e "$HERE/$f" ] || [ "$f" = "page.html" ] || { echo "нет $HERE/$f"; exit 1; }; done
  [ -e "$HERE/page-dense.html" ] || { echo "нет page-dense.html"; exit 1; }
  ls "$HERE"/fonts/*.woff2 >/dev/null || { echo "нет шрифтов"; exit 1; }
  [ -e "$REPO/tools/arb_stats/systemd/arb-turnover.service" ] || { echo "нет юнита arb-turnover.service"; exit 1; }
  say "шаг 0: проверки"
  sshx "set -e
    [ ! -e /data/perf/HOLD ] || { echo 'есть /data/perf/HOLD — идёт замер, выкладка отложена'; exit 1; }
    for f in $PAGE_FILES; do [ ! -e $PAGE/\$f.$TAG ] || { echo \"уже есть $PAGE/\$f.$TAG — сначала rollback\"; exit 1; }; done
    for f in $CODE_BACKUP; do [ ! -e $CODE/\$f.$TAG ] || { echo \"уже есть $CODE/\$f.$TAG — сначала rollback\"; exit 1; }; done
    [ ! -e $LIVE/state.pkl.$TAG ] || { echo 'уже есть state.pkl.$TAG — сначала rollback'; exit 1; }
    for p in $PORTS; do curl -sf --max-time 10 http://127.0.0.1:\$p/api/arb -o /dev/null || { echo \"порт \$p не отвечает\"; exit 1; }; done
    for u in $PRE_UNITS; do systemctl is-active \$u >/dev/null || { echo \"\$u не работает\"; exit 1; }; done"
  say "шаг 1: копии *.$TAG и запись прежнего состояния"
  sshx "set -e
    { echo \"at=\$(date -Is)\"; echo \"page_md5=\$(md5sum $PAGE/page.html | cut -c1-32)\"; echo \"serve_md5=\$(md5sum $PAGE/serve.py | cut -c1-32)\"
      echo \"api_md5=\$(md5sum $PAGE/arb_api.py | cut -c1-32)\"; echo \"daemon_md5=\$(md5sum $CODE/arb_daemon.py | cut -c1-32)\"
      echo \"common_md5=\$(md5sum $CODE/common.py | cut -c1-32)\"; echo \"live_md5=\$(md5sum $CODE/arb_live.py | cut -c1-32)\"; echo \"analyze_md5=\$(md5sum $CODE/analyze.py | cut -c1-32)\"; } > $MAN
    for f in $PAGE_FILES; do cp -p $PAGE/\$f $PAGE/\$f.$TAG; done
    for f in $CODE_BACKUP; do cp -p $CODE/\$f $CODE/\$f.$TAG; done"
  trap 'say "СБОЙ — автооткат"; rollback; exit 1' ERR
  say "шаг 2: служба оборота (код, юнит, bootstrap)"
  tar -cf - -C "$REPO/tools/arb_stats" $CODE_BACKUP $CODE_NEW | sshx "tar -xf - -C $CODE"
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 -m py_compile $CODE_BACKUP $CODE_NEW"
  tr -d '\r' < "$REPO/tools/arb_stats/systemd/arb-turnover.service" | sed "s#/opt/arb-tab/tools/arb_stats#$CODE#g; s#/data/arb-tab/live#$LIVE#g" | sshx "cat > /etc/systemd/system/$UNIT.service"
  t0="$(sshx 'date +%s')"
  sshx "systemctl daemon-reload && systemctl set-property --runtime $UNIT CPUQuota=$BOOT_QUOTA && systemctl enable --now $UNIT"
  say "жду turnover.json (bootstrap, до 20 мин)"
  for i in $(seq 1 60); do
    sleep 20
    ok="$(sshx "[ -s $LIVE/turnover.json ] && [ \$(stat -c %Y $LIVE/turnover.json) -ge $t0 ] && echo 1 || echo 0")"
    [ "$ok" = "1" ] && break
    sshx "systemctl is-active $UNIT >/dev/null" || { say "служба оборота упала"; sshx "journalctl -u $UNIT -n 20 --no-pager | cut -c1-240"; false; }
  done
  [ "$ok" = "1" ] || { say "turnover.json не появился за 20 мин"; false; }
  sshx "systemctl set-property --runtime $UNIT CPUQuota=100% && journalctl -u $UNIT -n 4 --no-pager | cut -c1-300"
  say "шаг 3: serve.py, arb_api.py, шрифты, перезапуск страниц"
  tar -cf - -C "$HERE" serve.py arb_api.py fonts | sshx "tar -xf - -C $PAGE"
  sshx "cd $PAGE && python3 -m py_compile serve.py arb_api.py && systemctl restart $PAGES && sleep 2 && for u in $PAGES; do systemctl is-active \$u; done"
  sshx "curl -sf --max-time 20 http://127.0.0.1:$FIRST_PORT/api/arb | python3 -c 'import json,sys; d=json.load(sys.stdin); n=len(d[\"rows\"]); v=sum(1 for r in d[\"rows\"] if \"v\" in r); print(\"строк\", n, \"с оборотом\", v, \"окно\", d.get(\"vwin\")); sys.exit(0 if v >= 0.9*n else 1)'"
  if [ "${GATE:-1}" = "1" ]; then gate_step; else say "Gate пропущен (GATE=0): код на диске с Gate, arb-live не перезапущен — потом: bash deploy_dense.sh gate --yes"; fi
  say "шаг 4: page.html"
  sshx "cat > $PAGE/page.html.new && mv $PAGE/page.html.new $PAGE/page.html" < "$HERE/page-dense.html"
  say "шаг 5: проверка"
  verify
  trap - ERR
  say "готово. Откат: bash deploy_dense.sh rollback"
}

case "${1:-}" in
  deploy) shift; deploy "$@" ;;
  gate) shift; gate_cmd "$@" ;;
  twin) shift; twin "$@" ;;
  rollback) rollback ;;
  verify) verify ;;
  status) status ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
