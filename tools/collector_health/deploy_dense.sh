#!/bin/bash
# Выкладка плотной вкладки «Арбитраж» (page-dense.html + оборот за сутки + шрифты Geist) на srv, страницы 8088 / 8089.
# НЕ ЗАПУСКАТЬ без слова владельца «да» на финальный макет (владелец 15.11: макет ещё не принят).
#
#   bash deploy_dense.sh deploy --yes   выкладка по шагам, при сбое после первой правки — автооткат
#   bash deploy_dense.sh rollback       вернуть прежние файлы, остановить arb-turnover, перезапустить страницы
#   bash deploy_dense.sh verify         проверка выложенного (/api/arb с оборотом, шрифты, 3 монеты против тикеров бирж)
#   bash deploy_dense.sh status         что сейчас на srv
#
# Порядок deploy (arb-live не перезапускается — рестарт прогревает все файлы книг с начала; страницы перезапускаются на ~1 с):
#   0. проверки: нет /data/perf/HOLD, копий *.prev-dense ещё нет, страницы отвечают; запись прежних md5 в deploy-dense.manifest
#   1. копии *.prev-dense: page.html, serve.py, arb_api.py (/data/turbo-probe/page), arb_daemon.py (/opt/arb-tab/tools/arb_stats)
#   2. код службы оборота (arb_turnover.py, parse_trades.py, arb_daemon.py с Reader.consume) + юнит arb-turnover.service — запуск ПЕРВЫМ:
#      bootstrap ≈ 4–5 мин на 3 ядрах, turnover.json появляется до перезапуска страниц
#   3. serve.py + arb_api.py + шрифты → перезапуск 8088 / 8089; проверка /fonts и v в /api/arb
#   4. page.html (читается по mtime — вступает в силу сразу)
#   5. проверка: оборот 3 монет (BTC, SOL, ZORA) против тикеров бирж; при LAYOUT=1 — клик-тест страницы с этого ПК
# Переменные: SRV (root@5.231.25.224), SSH_KEY, SSH_KNOWN_HOSTS (кириллица в HOME — ключ и known_hosts всегда явно), LAYOUT=1.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SRV="${SRV:-root@5.231.25.224}"
KEY="${SSH_KEY:-/c/Users/Георгий/.ssh/id_rsa}"
KH="${SSH_KNOWN_HOSTS:-/c/Users/Георгий/.ssh/known_hosts}"
PAGE=/data/turbo-probe/page
CODE=/opt/arb-tab/tools/arb_stats
LIVE=/data/arb-tab/live
TAG=prev-dense
MAN="$PAGE/deploy-dense.manifest"
UNIT=arb-turnover
PAGES="health-page-8088 health-soak-page"
PAGE_FILES="serve.py arb_api.py page.html"
CODE_BACKUP="arb_daemon.py"
CODE_NEW="arb_turnover.py parse_trades.py turnover_check.py turnover_verify.py"

sshx() { ssh -i "$KEY" -o UserKnownHostsFile="$KH" -o BatchMode=yes -o ConnectTimeout=15 "$SRV" "$@"; }
say() { echo "[$(date +%H:%M:%S)] $*"; }

status() {
  sshx "set -e
    for u in $UNIT $PAGES arb-live; do printf '%-20s %s\n' \$u \$(systemctl is-active \$u); done
    md5sum $PAGE/page.html $PAGE/serve.py $PAGE/arb_api.py $CODE/arb_daemon.py | cut -c1-12,33-
    ls $PAGE/*.$TAG $CODE/*.$TAG 2>/dev/null || echo 'копий $TAG нет'
    ls -la $LIVE/turnover.json $LIVE/turnover-state.pkl 2>&1 | cut -c1-120
    journalctl -u $UNIT -n 3 --no-pager 2>&1 | cut -c1-240 || true"
}

rollback() {
  set +e; trap - ERR
  say "откат"
  sshx "set -e
    for f in $PAGE_FILES; do [ -e $PAGE/\$f.$TAG ] && cp -p $PAGE/\$f.$TAG $PAGE/\$f.rolling && mv $PAGE/\$f.rolling $PAGE/\$f && echo \"вернул \$f\"; done
    for f in $CODE_BACKUP; do [ -e $CODE/\$f.$TAG ] && cp -p $CODE/\$f.$TAG $CODE/\$f.rolling && mv $CODE/\$f.rolling $CODE/\$f && echo \"вернул \$f\"; done
    systemctl stop $UNIT 2>/dev/null || true
    systemctl disable $UNIT 2>/dev/null || true
    rm -f /etc/systemd/system/$UNIT.service; systemctl daemon-reload
    for f in $CODE_NEW; do rm -f $CODE/\$f; done
    rm -rf $PAGE/fonts
    systemctl restart $PAGES
    sleep 2
    for u in $PAGES; do printf '%-20s %s\n' \$u \$(systemctl is-active \$u); done"
  before="$(sshx "grep '^page_md5=' $MAN 2>/dev/null | cut -d= -f2" || true)"
  now="$(sshx "md5sum $PAGE/page.html | cut -c1-32")"
  if [ -n "$before" ] && [ "$before" = "$now" ]; then say "page.html совпадает с записанным до выкладки ($before)"; else say "ВНИМАНИЕ: md5 page.html $now, до выкладки было '${before:-нет записи}'"; fi
  say "turnover.json и turnover-state.pkl в $LIVE оставлены (старый arb_api их не читает)"
}

verify() {
  say "проверка выложенного"
  for port in 8088 8089; do
    code="$(sshx "curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:$port/fonts/geist-latin-wght-normal.woff2")"
    [ "$code" = "200" ] || { say "FAIL: /fonts на $port → $code"; return 1; }
    sshx "curl -s --max-time 20 http://127.0.0.1:$port/ | grep -q 'Столбцы'" || { say "FAIL: на $port не плотная страница"; return 1; }
    say "порт $port: страница плотная, шрифты 200"
  done
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 turnover_verify.py http://127.0.0.1:8088/api/arb BTC,SOL,ZORA"
  if [ "${LAYOUT:-0}" = "1" ]; then
    say "клик-тест страницы с этого ПК (1280, 77 действий + «Столбцы»)"
    node "$HERE/perf/layout_test_shadcn.js" http://5.231.25.224:8088/ 1280
    node "$HERE/perf/layout_test_cols.js" http://5.231.25.224:8088/ 1280
  fi
}

deploy() {
  [ "${1:-}" = "--yes" ] || { echo "нужен явный флаг: deploy --yes (после слова владельца «да»)"; exit 2; }
  for f in $PAGE_FILES; do [ -e "$HERE/$f" ] || [ "$f" = "page.html" ] || { echo "нет $HERE/$f"; exit 1; }; done
  [ -e "$HERE/page-dense.html" ] || { echo "нет page-dense.html"; exit 1; }
  ls "$HERE"/fonts/*.woff2 >/dev/null || { echo "нет шрифтов"; exit 1; }
  [ -e "$REPO/tools/arb_stats/systemd/$UNIT.service" ] || { echo "нет юнита $UNIT"; exit 1; }
  say "шаг 0: проверки"
  sshx "set -e
    [ ! -e /data/perf/HOLD ] || { echo 'есть /data/perf/HOLD — идёт замер, выкладка отложена'; exit 1; }
    for f in $PAGE_FILES; do [ ! -e $PAGE/\$f.$TAG ] || { echo \"уже есть $PAGE/\$f.$TAG — сначала rollback\"; exit 1; }; done
    [ ! -e $CODE/arb_daemon.py.$TAG ] || { echo 'уже есть arb_daemon.py.$TAG'; exit 1; }
    for p in 8088 8089; do curl -sf --max-time 10 http://127.0.0.1:\$p/api/arb -o /dev/null || { echo \"порт \$p не отвечает\"; exit 1; }; done
    systemctl is-active arb-live >/dev/null || { echo 'arb-live не работает'; exit 1; }"
  say "шаг 1: копии *.$TAG и запись прежнего состояния"
  sshx "set -e
    { echo \"at=\$(date -Is)\"; echo \"page_md5=\$(md5sum $PAGE/page.html | cut -c1-32)\"; echo \"serve_md5=\$(md5sum $PAGE/serve.py | cut -c1-32)\"
      echo \"api_md5=\$(md5sum $PAGE/arb_api.py | cut -c1-32)\"; echo \"daemon_md5=\$(md5sum $CODE/arb_daemon.py | cut -c1-32)\"; } > $MAN
    for f in $PAGE_FILES; do cp -p $PAGE/\$f $PAGE/\$f.$TAG; done
    for f in $CODE_BACKUP; do cp -p $CODE/\$f $CODE/\$f.$TAG; done"
  trap 'say "СБОЙ — автооткат"; rollback; exit 1' ERR
  say "шаг 2: служба оборота (код, юнит, bootstrap)"
  tar -cf - -C "$REPO/tools/arb_stats" arb_daemon.py $CODE_NEW | sshx "tar -xf - -C $CODE"
  sshx "cd $CODE && PYTHONPATH=/opt/arb-tab/pylib python3 -m py_compile arb_daemon.py arb_turnover.py parse_trades.py turnover_verify.py"
  tr -d '' < "$REPO/tools/arb_stats/systemd/$UNIT.service" | sshx "cat > /etc/systemd/system/$UNIT.service"
  t0="$(sshx 'date +%s')"
  sshx "systemctl daemon-reload && systemctl enable --now $UNIT"
  say "жду turnover.json (bootstrap, до 15 мин)"
  for i in $(seq 1 45); do
    sleep 20
    ok="$(sshx "[ -s $LIVE/turnover.json ] && [ \$(stat -c %Y $LIVE/turnover.json) -ge $t0 ] && echo 1 || echo 0")"
    [ "$ok" = "1" ] && break
    sshx "systemctl is-active $UNIT >/dev/null" || { say "служба оборота упала"; sshx "journalctl -u $UNIT -n 20 --no-pager | cut -c1-240"; false; }
  done
  [ "$ok" = "1" ] || { say "turnover.json не появился за 15 мин"; false; }
  sshx "journalctl -u $UNIT -n 4 --no-pager | cut -c1-300"
  say "шаг 3: serve.py, arb_api.py, шрифты, перезапуск страниц"
  tar -cf - -C "$HERE" serve.py arb_api.py fonts | sshx "tar -xf - -C $PAGE"
  sshx "cd $PAGE && python3 -m py_compile serve.py arb_api.py && systemctl restart $PAGES && sleep 2 && for u in $PAGES; do systemctl is-active \$u; done"
  sshx "curl -sf --max-time 20 http://127.0.0.1:8088/api/arb | python3 -c 'import json,sys; d=json.load(sys.stdin); n=len(d[\"rows\"]); v=sum(1 for r in d[\"rows\"] if \"v\" in r); print(\"строк\", n, \"с оборотом\", v, \"окно\", d.get(\"vwin\")); sys.exit(0 if v >= 0.9*n else 1)'"
  say "шаг 4: page.html"
  sshx "cat > $PAGE/page.html.new && mv $PAGE/page.html.new $PAGE/page.html" < "$HERE/page-dense.html"
  say "шаг 5: проверка"
  verify
  trap - ERR
  say "готово. Откат: bash deploy_dense.sh rollback"
}

case "${1:-}" in
  deploy) shift; deploy "$@" ;;
  rollback) rollback ;;
  verify) verify ;;
  status) status ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
