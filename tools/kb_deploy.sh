#!/usr/bin/env bash
# TK-629: собрать сайт kb и выложить на srv:8091 (юнит kb-page, /data/pages/kb); образец — ml-page-8090. Запуск из любого места: bash tools/kb_deploy.sh
# Сначала `kb check` (красный — выкладка не идёт). Юнит создаётся при первом запуске; существующий не трогается (страница статическая, перезапуск не нужен).
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@5.231.25.224
PORT=8091
python tools/kb.py check | tail -3
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
python tools/kb.py site "$OUT"
tar -C "$OUT" -c . | ssh "${KEY[@]}" "$HOST" 'rm -rf /data/pages/kb_new && mkdir -p /data/pages/kb_new && tar -x -C /data/pages/kb_new'
ssh "${KEY[@]}" "$HOST" "PORT=$PORT bash -s" <<'REMOTE'
set -e
mkdir -p /data/pages/kb
rm -rf /data/pages/kb/c
cp -a /data/pages/kb_new/. /data/pages/kb/
rm -rf /data/pages/kb_new
systemctl is-active --quiet kb-page || systemd-run --unit=kb-page -p Restart=always -p Nice=19 --collect /usr/bin/python3 -m http.server "$PORT" --directory /data/pages/kb
sleep 1
curl -s -o /dev/null -w "kb-page HTTP %{http_code}\n" "http://127.0.0.1:$PORT/"
REMOTE
