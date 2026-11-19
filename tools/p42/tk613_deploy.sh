#!/usr/bin/env bash
# TK-613: выкладка вкладки «Прогресс» на srv:8090 — сборщик + таймер tk613-progress (раз в 60 с) + пересборка страницы из шаблона.
# Данные страница читает из /data/pages/ml/data/tasks.json (его пишет сборщик), поэтому после выкладки пересборка страницы для обновления цифр не нужна.
# Остальные данные страницы (progress/features/equity/shape.json) не трогаются: пересборка берёт то, что уже лежит на srv.
set -euo pipefail
cd "$(dirname "$0")"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@5.231.25.224
python -c "import ast,sys; ast.parse(open('tk613_collect.py', encoding='utf-8').read())"
T=$(mktemp -d)
for f in tk613_collect.py tk593_template.html tk613-progress.service tk613-progress.timer; do tr -d '\r' < "$f" > "$T/$f"; done
tar -c -C "$T" tk613_collect.py tk593_template.html tk613-progress.service tk613-progress.timer | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk613 && tar -x -C /data/pages/_src'
rm -rf "$T"
ssh "${KEY[@]}" "$HOST" bash -s <<'REMOTE'
set -e
cd /data/pages/_src
[ -f index.html.pre-tk613 ] || cp /data/pages/ml/index.html index.html.pre-tk613
cp tk613-progress.service tk613-progress.timer /etc/systemd/system/
systemctl daemon-reload
nice -n 19 python3 tk613_collect.py
/opt/venv/bin/python tk593_page.py /data/tk592/tree /data/tk592/pairs /data/pages/ml/data/curves.json tk593_template.html /data/pages/ml
systemctl enable --now tk613-progress.timer
systemctl list-timers tk613-progress.timer --no-pager | head -3
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8090/
curl -s http://127.0.0.1:8090/data/tasks.json | head -c 200; echo
REMOTE
