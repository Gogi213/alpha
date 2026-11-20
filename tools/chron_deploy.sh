#!/usr/bin/env bash
# TK-646: выложить летопись на srv — tools/chron.py и tools/kb.py → /data/chron/, ночной снимок (chron-backup.timer → Storage Box),
# сайт :8091 (юнит kb-page) = `chron.py serve`: статика kb из /data/pages/kb + /chron/ из БД. Запуск из любого места: bash tools/chron_deploy.sh
# БД (/data/chron/chron.sqlite) не трогается; создаётся пустой при первом обращении. Юнит kb-page перезапускается только если код изменился.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o BatchMode=yes)
HOST=root@5.231.25.224
tar -c tools/chron.py tools/kb.py tools/systemd/chron-backup.service tools/systemd/chron-backup.timer | ssh "${KEY[@]}" "$HOST" 'set -e
mkdir -p /data/chron/backup /data/chron/new && tar -x -C /data/chron/new
changed=0
for f in chron.py kb.py; do cmp -s /data/chron/new/tools/$f /data/chron/$f || { cp /data/chron/new/tools/$f /data/chron/$f.tmp && mv /data/chron/$f.tmp /data/chron/$f; changed=1; }; done
for u in chron-backup.service chron-backup.timer; do cmp -s /data/chron/new/tools/systemd/$u /etc/systemd/system/$u || { cp /data/chron/new/tools/systemd/$u /etc/systemd/system/$u; systemctl daemon-reload; }; done
rm -rf /data/chron/new
systemctl enable --now chron-backup.timer >/dev/null 2>&1
python3 /data/chron/chron.py stats | head -3
if ! systemctl show kb-page -p ExecStart | grep -q chron.py; then systemctl stop kb-page 2>/dev/null || true; changed=1; fi
if [ $changed = 1 ] || ! systemctl is-active --quiet kb-page; then
  systemctl stop kb-page 2>/dev/null || true
  systemd-run --unit=kb-page -p Restart=always -p Nice=19 --collect /usr/bin/python3 /data/chron/chron.py serve --port 8091 --static /data/pages/kb >/dev/null
fi
sleep 2
for p in / /chron/ /chron/p/; do curl -s -o /dev/null -w "kb-page $p HTTP %{http_code}\n" "http://127.0.0.1:8091$p"; done'
